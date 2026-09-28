"""Step 3: the report. System A (LLM as judge) and system B (DeepEval) side by side,
and how far they agree.

Writes evals/runs/<run>/report.md and scores.csv (one row per score, long format).

  .venv/bin/python -m evals.compare [--run NAME]
"""

import argparse
import csv
import json
import statistics
from collections import defaultdict

from .common import COMPANION_SCALES, LOWER_IS_BETTER, MODELS, SCALES, RunDir, goodness

TRACK_NAMES = {"heart": "For the heart", "deep": "Deep dive", "companion": "Talk it over (companion)"}


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.fmean(xs), 2) if xs else None


def fmt(x, digits=2):
    return "–" if x is None else f"{x:.{digits}f}"


def load(run: RunDir):
    """Every score as a row: system, method, judge, model, track, item, scale, value (1-7 or 0-1)."""
    rows = []
    for f in sorted((run.dir / "llm_judge").glob("*.json")):
        j = json.loads(f.read_text())
        model, track, item = j["key"]
        for scale, v in j["scores"].items():
            rows.append({"system": "A", "method": "llm_judge", "judge": j["judge"], "model": model, "track": track,
                         "item": item, "scale": scale, "value": float(v)})
    for f in sorted((run.dir / "deepeval").glob("*.json")):
        model, track, item = f.stem.split("__")
        for name, entry in json.loads(f.read_text()).items():
            if "error" in entry:
                continue
            method, scale = name.split(":", 1)
            value = entry.get("rubric", entry["score"])
            rows.append({"system": "B", "method": method, "judge": method, "model": model, "track": track,
                         "item": item, "scale": scale, "value": float(value)})
    return rows


def costs(run: RunDir):
    gen = defaultdict(lambda: {"usd": 0.0, "seconds": [], "words": []})
    for s in run.samples("samples"):
        g = gen[(s["model"], s["track"])]
        g["usd"] += s["usd"]; g["seconds"].append(s["seconds"]); g["words"].append(s["words"])
    for c in run.samples("conversations"):
        g = gen[(c["model"], "companion")]
        g["usd"] += c["usd"]; g["seconds"].append(c["seconds"] / max(1, len(c["turns"]) // 2))
    judge_a = sum(json.loads(f.read_text()).get("usd", 0) for f in (run.dir / "llm_judge").glob("*.json"))
    judge_b = sum(e.get("usd", 0) for f in (run.dir / "deepeval").glob("*.json")
                  for e in json.loads(f.read_text()).values())
    return gen, judge_a, judge_b


def rubric_table(rows, method_filter, track, scales, models):
    """Scales down the side, models across: the mean over items (and judges)."""
    cell = defaultdict(list)
    for r in rows:
        if method_filter(r) and r["track"] == track and r["scale"] in scales:
            cell[(r["scale"], r["model"])].append(r["value"])
    if not cell:
        return None
    lines = ["| Scale | " + " | ".join(MODELS[m][2] for m in models) + " |", "| --- |" + " --- |" * len(models)]
    for scale in scales:
        mark = " ↓" if scale in LOWER_IS_BETTER else ""
        lines.append(f"| {scale.replace('_', ' ')}{mark} | " + " | ".join(fmt(mean(cell[(scale, m)])) for m in models) + " |")
    overall = [mean([goodness(s, mean(cell[(s, m)]) or 0) for s in scales if cell[(s, m)]]) for m in models]
    lines.append("| **overall (higher is better)** | " + " | ".join(f"**{fmt(o)}**" for o in overall) + " |")
    return "\n".join(lines), dict(zip(models, overall))


def per_item(rows, method_filter, scales):
    """{(model, track, item): {scale: mean value}} for one method."""
    acc = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if method_filter(r) and r["scale"] in scales:
            acc[(r["model"], r["track"], r["item"])][r["scale"]].append(r["value"])
    return {k: {s: statistics.fmean(v) for s, v in d.items()} for k, d in acc.items()}


def agreement(a, b, scales):
    """Per scale: Spearman correlation across items scored by both, and the mean gap."""
    out = []
    for scale in scales:
        pairs = [(a[k][scale], b[k][scale]) for k in a if k in b and scale in a[k] and scale in b[k]]
        if len(pairs) < 4:
            continue
        xs, ys = zip(*pairs)
        try:
            rho = statistics.correlation(xs, ys, method="ranked")
        except statistics.StatisticsError:
            rho = None  # one side gave every item the same score
        out.append((scale, len(pairs), rho, statistics.fmean(y - x for x, y in pairs)))
    return out


def ranking(overall: dict) -> list[str]:
    return [m for m, v in sorted(overall.items(), key=lambda kv: -(kv[1] or 0)) if v is not None]


def write(run: RunDir) -> str:
    rows = load(run)
    models = sorted({r["model"] for r in rows} | {s["model"] for s in run.samples("samples")}, key=list(MODELS).index)
    judges = sorted({r["judge"] for r in rows if r["system"] == "A"})
    gen, judge_a, judge_b = costs(run)
    methods_b = sorted({r["method"] for r in rows if r["system"] == "B"})
    out = [f"# Model evals: {run.dir.name}", "",
           f"Contestants: {', '.join(MODELS[m][2] for m in models)}. System A judges: "
           f"{', '.join(MODELS[j][2] for j in judges) or 'none yet'}. System B methods: {', '.join(methods_b) or 'none yet'}.",
           "Scores are 1 to 7, averaged over passages (or conversations) and judges; ↓ marks scales where lower is better. "
           "\"Overall\" averages every scale with the ↓ ones turned around (8 minus the score).", ""]

    out += ["## Writing: cost and speed", "", "| Model | Track | Cost per piece | Seconds per piece (per reply for the companion) | Words |",
            "| --- | --- | --- | --- | --- |"]
    for (m, t), g in sorted(gen.items(), key=lambda kv: (list(MODELS).index(kv[0][0]), kv[0][1])):
        n = len(g["seconds"])
        out.append(f"| {MODELS[m][2]} | {TRACK_NAMES[t]} | ${g['usd'] / n:.4f} | {statistics.fmean(g['seconds']):.0f} | "
                   f"{fmt(mean(g['words']), 0) if g['words'] else '–'} |")
    out += ["", f"Judging cost: system A ${judge_a:.2f}, system B ${judge_b:.2f} (Jev bills separately and is not included).", ""]

    rank = {}
    for track, scales in (("heart", SCALES), ("deep", SCALES), ("companion", COMPANION_SCALES)):
        out += [f"## {TRACK_NAMES[track]}", ""]
        for label, filt, key in (("System A: LLM as judge (both judges)", lambda r: r["system"] == "A", "A"),
                                 ("System A: no judge scoring its own model", lambda r: r["system"] == "A" and r["judge"] != r["model"], "A:no-self"),
                                 *[(f"System A: judged by {MODELS[j][2]} alone", (lambda j: lambda r: r["judge"] == j)(j), f"A:{j}")
                                   for j in judges if len(judges) > 1],
                                 ("System B: DeepEval G-Eval", lambda r: r["method"] == "geval", "B:geval"),
                                 ("System B: DeepEval JevEval (Jev)", lambda r: r["method"] == "jev", "B:jev")):
            made = rubric_table(rows, filt, track, scales, models)
            if made:
                table, overall = made
                rank[(track, key)] = ranking(overall)
                out += [f"### {label}", "", table, ""]
        other = defaultdict(list)
        for r in rows:
            if r["system"] == "B" and r["track"] == track and r["method"] in ("builtin", "rules", "jev") and r["scale"] not in scales:
                other[(r["method"] + ": " + r["scale"], r["model"])].append(r["value"])
        if other:
            names = sorted({k[0] for k in other})
            out += ["### System B: other DeepEval metrics (0 to 1, higher is better)", "",
                    "| Metric | " + " | ".join(MODELS[m][2] for m in models) + " |", "| --- |" + " --- |" * len(models)]
            out += [f"| {n} | " + " | ".join(fmt(mean(other[(n, m)])) for m in models) + " |" for n in names]
            out.append("")

    out += ["## Do the two systems agree?", "",
            "Spearman's ρ ranks the items by each system and correlates the ranks: 1 means the same order, 0 no relation. "
            "The gap is B minus A on the 1-7 scale.", ""]
    for b_method in ("geval", "jev"):
        for track, scales in (("heart", SCALES), ("deep", SCALES), ("companion", COMPANION_SCALES)):
            a = per_item(rows, lambda r: r["system"] == "A" and r["track"] == track, scales)
            b = per_item(rows, lambda r: r["method"] == b_method and r["track"] == track, scales)
            agree = agreement(a, b, scales)
            if not agree:
                continue
            out += [f"### A vs B {b_method}: {TRACK_NAMES[track]}", "", "| Scale | Items | Spearman ρ | Gap (B − A) |", "| --- | --- | --- | --- |"]
            out += [f"| {s.replace('_', ' ')} | {n} | {fmt(rho)} | {gap:+.2f} |" for s, n, rho, gap in agree]
            out.append("")
    out += ["### Model rankings by overall score", "", "| Track | Ranking | Order |", "| --- | --- | --- |"]
    for (track, key), order in rank.items():
        out.append(f"| {TRACK_NAMES[track]} | {key} | {' > '.join(MODELS[m][2] for m in order)} |")
    out.append("")

    out += ["## What the judges said", ""]
    for f in sorted((run.dir / "llm_judge").glob("*.json")):
        j = json.loads(f.read_text())
        m, t, item = j["key"]
        out.append(f"- **{MODELS[m][2]}**, {TRACK_NAMES[t]}, {item} (judge {MODELS[j['judge']][2]}): "
                   f"+ {j.get('strength', '')} − {j.get('weakness', '')}")
    out.append("")

    (run.dir / "report.md").write_text("\n".join(out))
    with open(run.dir / "scores.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["system", "method", "judge", "model", "track", "item", "scale", "value"])
        w.writeheader(); w.writerows(rows)
    return str(run.dir / "report.md")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", default="default")
    print(write(RunDir(parser.parse_args().run)))
