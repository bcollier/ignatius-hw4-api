"""How far the system A judges agree with each other, before trusting their averages.

For each track and scale: Krippendorff's alpha across the judges (interval scale; 1 is
perfect agreement, 0 is no better than chance; above about 0.67 is usually treated as
usable, above 0.8 as reliable). For each judge: how lenient it is (its mean, with the
lower-is-better scales turned around), how much of the 1-7 range it uses, and how its
overall scores correlate with each other judge's, item by item. And how each judge
ranks the contestants.

  .venv/bin/python -m evals.reliability [--run NAME] [--judges gemini,muse,llama-scout]
Writes evals/runs/<run>/reliability.md.
"""

import argparse
import json
import statistics
from collections import defaultdict
from itertools import combinations

from .common import COMPANION_SCALES, MODELS, SCALES, RunDir, goodness

TRACKS = {"heart": ("For the heart", SCALES), "deep": ("Deep dive", SCALES), "companion": ("Talk it over", COMPANION_SCALES)}


def alpha(units: list[list[float]]) -> float | None:
    """Krippendorff's alpha, interval metric. `units` holds each item's scores (one per judge)."""
    units = [u for u in units if len(u) >= 2]
    values = [v for u in units for v in u]
    n = len(values)
    if n < 4:
        return None
    observed = sum(sum((a - b) ** 2 for a in u for b in u) / (len(u) - 1) for u in units) / n
    expected = sum((a - b) ** 2 for a in values for b in values) / (n * (n - 1))
    return None if expected == 0 else 1 - observed / expected


def _units(scores: dict, judges: list[str], items: list) -> list[list[float]]:
    return [[scores[(j, it)] for j in judges if (j, it) in scores] for it in items]


def _standardized(scores: dict, judges: list[str]) -> dict:
    """Each judge's scores as distances from its own mean, in its own standard deviations:
    what's left is only how it orders the items, not how generous it is."""
    out = {}
    for j in judges:
        mine = {k: v for k, v in scores.items() if k[0] == j}
        if len(mine) < 2:
            continue
        mean, sd = statistics.fmean(mine.values()), statistics.pstdev(mine.values())
        out.update({k: (v - mean) / sd if sd else 0.0 for k, v in mine.items()})
    return out


def fmt(x, d=2):
    return "–" if x is None else f"{x:.{d}f}"


def load(run: RunDir, judges: list[str]) -> dict:
    """{(judge, model, track, item): {scale: score}}"""
    out = {}
    for f in (run.dir / "llm_judge").glob("*.json"):
        j = json.loads(f.read_text())
        if j["judge"] in judges:
            out[(j["judge"], *j["key"])] = {k: float(v) for k, v in j["scores"].items()}
    return out


def overall(scores: dict, scales: list[str]) -> float:
    return statistics.fmean(goodness(s, scores[s]) for s in scales if s in scores)


def write(run: RunDir, judges: list[str]) -> str:
    data = load(run, judges)
    lines = [f"# Judge reliability: {run.dir.name}", "",
             f"Judges: {', '.join(MODELS[j][2] for j in judges)}. Every judge scored every piece blind, 1 to 7.", ""]

    lines += ["## Each judge", "",
              "Mean: its average score with the lower-is-better scales turned around (higher = more lenient). "
              "Spread: the standard deviation of its overall scores across items (higher = it tells pieces apart more). "
              "Range: the lowest and highest single scores it gave.", "",
              "| Judge | Items | Mean | Spread | Range |", "| --- | --- | --- | --- | --- |"]
    for j in judges:
        mine = {k: v for k, v in data.items() if k[0] == j}
        overalls = [overall(v, TRACKS[k[2]][1]) for k, v in mine.items()]
        singles = [x for v in mine.values() for x in v.values()]
        if overalls:
            lines.append(f"| {MODELS[j][2]} | {len(mine)} | {statistics.fmean(overalls):.2f} | "
                         f"{statistics.pstdev(overalls):.2f} | {min(singles):.0f}–{max(singles):.0f} |")
    lines.append("")

    lines += ["## Do the judges agree? Krippendorff's alpha", "",
              "Per scale, across all the judges above. 1 = they agree perfectly; 0 = no better than chance; "
              "below 0 = they systematically disagree. Rough guide: 0.8+ reliable, 0.67–0.8 tentative, below 0.67 unreliable. "
              "The second column first removes each judge's own leniency and spread, so it shows only whether the judges "
              "put the pieces in the same order.", ""]
    for track, (name, scales) in TRACKS.items():
        items = sorted({k[1:] for k in data if k[2] == track})
        if not items:
            continue
        rows = []
        for s in scales:
            raw = {(j, it): data[(j, *it)][s] for j in judges for it in items if (j, *it) in data and s in data[(j, *it)]}
            rows.append((s, alpha(_units(raw, judges, items)), alpha(_units(_standardized(raw, judges), judges, items))))
        raw = {(j, it): overall(data[(j, *it)], scales) for j in judges for it in items if (j, *it) in data}
        lines += [f"### {name} ({len(items)} items)", "",
                  "| Scale | Alpha | Alpha, each judge's leniency removed |", "| --- | --- | --- |"]
        lines += [f"| {s.replace('_', ' ')} | {fmt(a)} | {fmt(z)} |" for s, a, z in rows]
        lines += [f"| **overall** | **{fmt(alpha(_units(raw, judges, items)))}** | "
                  f"**{fmt(alpha(_units(_standardized(raw, judges), judges, items)))}** |", ""]

    lines += ["## Judge against judge", "",
              "Spearman correlation of each pair's overall scores, item by item, all tracks together "
              "(1 = they put the pieces in the same order).", "",
              "| | " + " | ".join(MODELS[j][2] for j in judges) + " |", "| --- |" + " --- |" * len(judges)]
    per_item = defaultdict(dict)
    for (j, model, track, item), v in data.items():
        per_item[(model, track, item)][j] = overall(v, TRACKS[track][1])
    for a in judges:
        cells = []
        for b in judges:
            pairs = [(v[a], v[b]) for v in per_item.values() if a in v and b in v]
            try:
                cells.append("1" if a == b else fmt(statistics.correlation(*zip(*pairs), method="ranked")))
            except (statistics.StatisticsError, ValueError):
                cells.append("–")
        lines.append(f"| {MODELS[a][2]} | " + " | ".join(cells) + " |")
    lines.append("")

    lines += ["## How each judge ranks the contestants", "", "Overall score per contestant (all tracks), by judge.", ""]
    models = sorted({k[1] for k in data}, key=list(MODELS).index)
    lines += ["| Judge | " + " | ".join(MODELS[m][2] for m in models) + " |", "| --- |" + " --- |" * len(models)]
    for j in judges + ["all"]:
        cells = []
        for m in models:
            xs = [overall(v, TRACKS[k[2]][1]) for k, v in data.items() if k[1] == m and (j == "all" or k[0] == j)]
            cells.append(fmt(statistics.fmean(xs)) if xs else "–")
        lines.append(f"| {'**all judges**' if j == 'all' else MODELS[j][2]} | " + " | ".join(cells) + " |")
    lines.append("")

    path = run.dir / "reliability.md"
    path.write_text("\n".join(lines))
    return str(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", default="default")
    parser.add_argument("--judges", default="gemini,muse,llama-scout")
    args = parser.parse_args()
    print(write(RunDir(args.run), args.judges.split(",")))
