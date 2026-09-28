"""The statistics behind the eval analysis page: are these judges measuring anything?

Reads every system A judgment in a run and writes one JSON file for the web app's
debug-only Evals page (?evals). For each track (For the heart, Deep dive, Talk it over):

- Distributions: each judge's scores on each scale, 1 to 7.
- Each judge: leniency (mean, lower-is-better scales turned around), spread, how often
  it gives a 6 or 7 (the ceiling), and how often it failed to return scores.
- Agreement, per scale: Krippendorff's alpha (interval), raw and with each judge's own
  mean and spread removed; ICC(2,1), how reliable one judge is, and ICC(2,k), how
  reliable the average of all k judges is (the number the reports use); mean pairwise
  Cohen's kappa (quadratic weights), exact agreement and agreement within one point;
  and, by Spearman-Brown, how many judges would bring the average to 0.8.
- Judge against judge: weighted kappa, agreement, Spearman correlation of overall
  scores, and the mean difference (one judge's bias against another).
- Redundancy: correlations between the scales (item means across judges) and the share
  of their variance a single factor explains: do the nine fruits say nine things?
- Discrimination: how much of each scale's variation is which model wrote the piece
  (eta squared across models, on item means).
- Models: mean overall score per track with a 95% bootstrap interval, pairwise
  differences with intervals, and self-preference (a judge rating its own model).
- Power: how many passages per model it would take to see a real difference.

  .venv/bin/python -m evals.analysis [--run NAME] [--judges gemini,muse,llama-scout] [--out PATH]
"""

import argparse
import json
import random
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import numpy as np

from .common import COMPANION_SCALES, LOWER_IS_BETTER, MODELS, SCALES, RunDir, goodness
from .reliability import alpha

TRACKS = {"heart": ("For the heart", SCALES), "deep": ("Deep dive", SCALES), "companion": ("Talk it over", COMPANION_SCALES)}
GROUPS = {"Quality": ["emotionally_engaging", "thoughtful", "well_researched", "encouraging"],
          "Problems": ["ai_jargon", "too_vague", "theological_disagreement"],
          "Fruits of the Spirit": ["love", "joy", "peace", "patience", "kindness", "goodness", "faithfulness", "gentleness", "self_control"],
          "Theological virtues": ["faith", "hope", "charity"],
          "Companion": ["listening", "one_question", "restraint", "warmth", "spiritual_depth", "safety"]}
BOOTSTRAP = 4000
WEB_OUT = Path.home() / "Code" / "ignatius-hw4-web" / "evals"
rng = random.Random(27)


def r3(x):
    return None if x is None or (isinstance(x, float) and (x != x)) else round(float(x), 3)


# ---------------------------------------------------------------- agreement statistics

def weighted_kappa(a: list[int], b: list[int], k: int = 7) -> float | None:
    """Cohen's kappa with quadratic weights for two raters on a 1..k scale."""
    if len(a) < 3:
        return None
    observed = np.zeros((k, k))
    for x, y in zip(a, b):
        observed[int(x) - 1, int(y) - 1] += 1
    observed /= observed.sum()
    expected = np.outer(observed.sum(1), observed.sum(0))
    i, j = np.indices((k, k))
    w = (i - j) ** 2 / (k - 1) ** 2
    denom = (w * expected).sum()
    return None if denom == 0 else 1 - (w * observed).sum() / denom


def icc(matrix: np.ndarray) -> tuple[float | None, float | None]:
    """ICC(2,1) and ICC(2,k): two-way random effects, absolute agreement. Rows are items,
    columns judges, every cell filled."""
    n, k = matrix.shape
    if n < 3 or k < 2:
        return None, None
    grand = matrix.mean()
    ss_rows = k * ((matrix.mean(1) - grand) ** 2).sum()
    ss_cols = n * ((matrix.mean(0) - grand) ** 2).sum()
    ss_err = ((matrix - grand) ** 2).sum() - ss_rows - ss_cols
    msr, msc, mse = ss_rows / (n - 1), ss_cols / (k - 1), ss_err / ((n - 1) * (k - 1))
    single_den = msr + (k - 1) * mse + k * (msc - mse) / n
    average_den = msr + (msc - mse) / n
    return (None if single_den == 0 else (msr - mse) / single_den,
            None if average_den == 0 else (msr - mse) / average_den)


def judges_for(target: float, one: float | None) -> float | None:
    """Spearman-Brown: judges needed for the average to reach `target` reliability."""
    if one is None or one <= 0:
        return None
    return target * (1 - one) / (one * (1 - target))


def standardized(scores: dict, judges: list[str]) -> dict:
    out = {}
    for j in judges:
        mine = {k: v for k, v in scores.items() if k[0] == j}
        if len(mine) < 2:
            continue
        m, sd = statistics.fmean(mine.values()), statistics.pstdev(mine.values())
        out.update({k: (v - m) / sd if sd else 0.0 for k, v in mine.items()})
    return out


def units(scores: dict, judges: list[str], items: list) -> list[list[float]]:
    return [[scores[(j, it)] for j in judges if (j, it) in scores] for it in items]


def spearman(x, y) -> float | None:
    try:
        return statistics.correlation(x, y, method="ranked")
    except (statistics.StatisticsError, ValueError):
        return None


def pearson(x, y) -> float | None:
    try:
        return statistics.correlation(x, y)
    except (statistics.StatisticsError, ValueError):
        return None


PERMUTATIONS = 2000


def eta_squared(item_means: dict) -> tuple[float | None, float | None, float | None]:
    """How much of a scale's variation across items is which model wrote them (eta squared),
    its permutation p-value (shuffling which model wrote what), and the mean eta squared
    under that shuffling: what it comes to by chance with this many items and models."""
    if len(item_means) < 4:
        return None, None, None
    labels = [it[0] for it in item_means]
    values = list(item_means.values())

    def eta(labs):
        grand = statistics.fmean(values)
        sst = sum((v - grand) ** 2 for v in values)
        if not sst:
            return 0.0
        groups = defaultdict(list)
        for lab, v in zip(labs, values):
            groups[lab].append(v)
        return sum(len(g) * (statistics.fmean(g) - grand) ** 2 for g in groups.values()) / sst
    observed = eta(labels)
    shuffled = labels[:]
    null = []
    for _ in range(PERMUTATIONS):
        rng.shuffle(shuffled)
        null.append(eta(shuffled))
    return observed, (1 + sum(x >= observed for x in null)) / (PERMUTATIONS + 1), statistics.fmean(null)


def bootstrap_mean(values: list[float]) -> tuple[float, float, float]:
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(BOOTSTRAP))
    return statistics.fmean(values), means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP)]


def bootstrap_diff(a: list[float], b: list[float]) -> tuple[float, float, float]:
    diffs = sorted(statistics.fmean(rng.choices(a, k=len(a))) - statistics.fmean(rng.choices(b, k=len(b)))
                   for _ in range(BOOTSTRAP))
    return statistics.fmean(a) - statistics.fmean(b), diffs[int(0.025 * BOOTSTRAP)], diffs[int(0.975 * BOOTSTRAP)]


# ---------------------------------------------------------------- the analysis

def analyse(run: RunDir, judges: list[str]) -> dict:
    rows = [json.loads(f.read_text()) for f in sorted((run.dir / "llm_judge").glob("*.json"))]
    rows = [r for r in rows if r["judge"] in judges]
    data = {(r["judge"], *r["key"]): {k: float(v) for k, v in r["scores"].items()} for r in rows}
    # A failure is an item some judge scored and this one didn't (pieces written after the
    # judging ran aren't counted against anyone).
    expected = sorted({k[1:] for k in data})
    models = sorted({k[1] for k in data}, key=list(MODELS).index)

    out = {"run": run.dir.name, "made": datetime.now(timezone.utc).isoformat(timespec="minutes"),
           "judges": [{"id": j, "label": MODELS[j][2]} for j in judges],
           "models": [{"id": m, "label": MODELS[m][2]} for m in models],
           "tracks": [{"id": t, "label": name, "scales": sc} for t, (name, sc) in TRACKS.items()],
           "lower_is_better": sorted(LOWER_IS_BETTER), "groups": GROUPS,
           "judgments": [{"judge": r["judge"], "model": r["key"][0], "track": r["key"][1], "item": r["key"][2],
                          "scores": r["scores"], "strength": r.get("strength", ""), "weakness": r.get("weakness", ""),
                          "usd": r.get("usd", 0)} for r in rows]}

    # each judge
    judge_stats = []
    for j in judges:
        mine = {k: v for k, v in data.items() if k[0] == j}
        overalls = [statistics.fmean(goodness(s, v[s]) for s in TRACKS[k[2]][1] if s in v) for k, v in mine.items()]
        singles = [goodness(s, x) for k, v in mine.items() for s, x in v.items()]
        missing = [e for e in expected if (j, *e) not in data]
        judge_stats.append({"judge": j, "items": len(mine), "expected": len(expected), "failed": len(missing),
                            "mean": r3(statistics.fmean(overalls)) if overalls else None,
                            "sd": r3(statistics.pstdev(overalls)) if overalls else None,
                            "ceiling": r3(sum(x >= 6 for x in singles) / len(singles)) if singles else None,
                            "top": r3(sum(x == 7 for x in singles) / len(singles)) if singles else None,
                            "usd": r3(sum(r.get("usd", 0) for r in rows if r["judge"] == j))})
    out["judge_stats"] = judge_stats

    tracks_out = {}
    for t, (name, scales) in TRACKS.items():
        items = sorted({k[1:] for k in data if k[2] == t})
        if not items:
            continue
        complete = [it for it in items if all((j, *it) in data for j in judges)]
        per_scale = []
        for s in scales:
            raw = {(j, it): data[(j, *it)][s] for j in judges for it in items if (j, *it) in data and s in data[(j, *it)]}
            hist = {j: [sum(1 for (jj, _), v in raw.items() if jj == j and int(v) == n) for n in range(1, 8)] for j in judges}
            mat = np.array([[data[(j, *it)][s] for j in judges] for it in complete if all(s in data[(j, *it)] for j in judges)])
            one, avg = icc(mat) if len(mat) else (None, None)
            kappas, exact, within = [], [], []
            for a, b in combinations(judges, 2):
                pa = [(raw[(a, it)], raw[(b, it)]) for it in items if (a, it) in raw and (b, it) in raw]
                if pa:
                    xs, ys = zip(*pa)
                    k = weighted_kappa(xs, ys)
                    if k is not None:
                        kappas.append(k)
                    exact.append(statistics.fmean(x == y for x, y in pa))
                    within.append(statistics.fmean(abs(x - y) <= 1 for x, y in pa))
            item_means = {it: statistics.fmean(goodness(s, raw[(j, it)]) for j in judges if (j, it) in raw)
                          for it in items if any((j, it) in raw for j in judges)}
            allv = list(item_means.values())
            eta, p_eta, null_eta = eta_squared(item_means)
            top2 = [v for v in raw.values()]
            per_scale.append({
                "scale": s, "lower_is_better": s in LOWER_IS_BETTER, "hist": hist,
                "mean": r3(statistics.fmean(allv)) if allv else None,
                "alpha": r3(alpha(units(raw, judges, items))),
                "alpha_std": r3(alpha(units(standardized(raw, judges), judges, items))),
                "icc1": r3(one), "icck": r3(avg), "judges_for_08": r3(judges_for(0.8, one)),
                "kappa": r3(statistics.fmean(kappas)) if kappas else None,
                "exact": r3(statistics.fmean(exact)) if exact else None,
                "within1": r3(statistics.fmean(within)) if within else None,
                "eta2": r3(eta), "eta2_p": r3(p_eta), "eta2_null": r3(null_eta),
                "ceiling": r3(statistics.fmean((goodness(s, v) >= 6) for v in top2)) if top2 else None,
                "judge_means": {j: r3(statistics.fmean(v for (jj, _), v in raw.items() if jj == j))
                                for j in judges if any(jj == j for jj, _ in raw)},
                "item_sd": r3(statistics.pstdev(allv)) if len(allv) > 1 else None,
            })

        # overall score per judgment, and the per-item composite (mean over judges)
        overall = {(j, it): statistics.fmean(goodness(s, data[(j, *it)][s]) for s in scales if s in data[(j, *it)])
                   for j in judges for it in items if (j, *it) in data}
        composite = {it: statistics.fmean(overall[(j, it)] for j in judges if (j, it) in overall) for it in items}
        omat = np.array([[overall[(j, it)] for j in judges] for it in complete])
        one, avg = icc(omat) if len(omat) else (None, None)

        # judge against judge
        # Each pair's agreement is measured scale by scale, then averaged: pooling scales would
        # reward two judges merely for knowing which end of the range each scale lives at.
        pairs = []
        for a, b in combinations(judges, 2):
            per = []
            for s in scales:
                sa = [(data[(a, *it)][s], data[(b, *it)][s]) for it in items
                      if (a, *it) in data and (b, *it) in data and s in data[(a, *it)] and s in data[(b, *it)]]
                if len(sa) >= 3:
                    xs, ys = zip(*sa)
                    per.append((weighted_kappa(xs, ys), statistics.fmean(x == y for x, y in sa),
                                statistics.fmean(abs(x - y) <= 1 for x, y in sa)))
            oa = [(overall[(a, it)], overall[(b, it)]) for it in items if (a, it) in overall and (b, it) in overall]
            if not per:
                continue
            kap = [k for k, _, _ in per if k is not None]
            pairs.append({"a": a, "b": b, "kappa": r3(statistics.fmean(kap)) if kap else None,
                          "exact": r3(statistics.fmean(e for _, e, _ in per)),
                          "within1": r3(statistics.fmean(w for _, _, w in per)),
                          "spearman": r3(spearman(*zip(*oa))) if len(oa) > 3 else None,
                          "bias": r3(statistics.fmean(y - x for x, y in oa)) if oa else None, "n": len(oa)})

        # redundancy between scales
        item_scale = {s: [statistics.fmean(goodness(s, data[(j, *it)][s]) for j in judges if (j, *it) in data and s in data[(j, *it)])
                          for it in items] for s in scales}
        corr = [[r3(pearson(item_scale[a], item_scale[b])) if a != b else 1.0 for b in scales] for a in scales]
        cm = np.array([[c if c is not None else 0.0 for c in row] for row in corr])
        eig = sorted(np.linalg.eigvalsh(cm).tolist(), reverse=True)
        first = eig[0] / sum(eig) if sum(eig) else None
        fruits = [s for s in GROUPS["Fruits of the Spirit"] if s in scales]
        fruit_r = [cm[scales.index(a), scales.index(b)] for a, b in combinations(fruits, 2)]

        # models
        model_rows = []
        for m in models:
            vals = [composite[it] for it in items if it[0] == m]
            if len(vals) < 2:
                continue
            mean, lo, hi = bootstrap_mean(vals)
            by_judge = {j: r3(statistics.fmean(overall[(j, it)] for it in items if it[0] == m and (j, it) in overall))
                        for j in judges if any((j, it) in overall for it in items if it[0] == m)}
            model_rows.append({"model": m, "n": len(vals), "mean": r3(mean), "lo": r3(lo), "hi": r3(hi),
                               "by_judge": by_judge, "values": [r3(v) for v in vals]})
        diffs = []
        for a, b in combinations([r["model"] for r in model_rows], 2):
            va = [composite[it] for it in items if it[0] == a]
            vb = [composite[it] for it in items if it[0] == b]
            d, lo, hi = bootstrap_diff(va, vb)
            diffs.append({"a": a, "b": b, "diff": r3(d), "lo": r3(lo), "hi": r3(hi)})
        selfpref = []
        for j in judges:
            if j not in models:
                continue
            own = [overall[(j, it)] - statistics.fmean(overall[(o, it)] for o in judges if o != j and (o, it) in overall)
                   for it in items if it[0] == j and (j, it) in overall and any((o, it) in overall for o in judges if o != j)]
            others = [overall[(j, it)] - statistics.fmean(overall[(o, it)] for o in judges if o != j and (o, it) in overall)
                      for it in items if it[0] != j and (j, it) in overall and any((o, it) in overall for o in judges if o != j)]
            if own and others:
                selfpref.append({"judge": j, "own": r3(statistics.fmean(own)), "others": r3(statistics.fmean(others)),
                                 "preference": r3(statistics.fmean(own) - statistics.fmean(others))})

        # power: passages per model to detect a difference of 0.2 or 0.5 (two-sided 0.05, power 0.8)
        within_sd = [statistics.pstdev([composite[it] for it in items if it[0] == m])
                     for m in models if sum(1 for it in items if it[0] == m) > 1]
        sd = statistics.fmean(within_sd) if within_sd else None
        power = {str(d): (None if not sd else int(np.ceil(2 * (1.96 + 0.84) ** 2 * sd ** 2 / d ** 2))) for d in (0.1, 0.2, 0.5)}

        tracks_out[t] = {"label": name, "items": len(items), "complete": len(complete), "scales": per_scale,
                         "overall": {"alpha": r3(alpha(units(overall, judges, items))),
                                     "alpha_std": r3(alpha(units(standardized(overall, judges), judges, items))),
                                     "icc1": r3(one), "icck": r3(avg), "judges_for_08": r3(judges_for(0.8, one))},
                         "pairs": pairs, "corr": {"scales": scales, "matrix": corr},
                         "first_factor": r3(first), "fruit_mean_r": r3(statistics.fmean(fruit_r)) if fruit_r else None,
                         "models": model_rows, "diffs": diffs, "self_preference": selfpref,
                         "within_model_sd": r3(sd), "power": power}
    out["tracks_data"] = tracks_out
    out["pieces"] = pieces(run)
    out["text_agreement"] = text_agreement(rows, judges)
    return out


# ---------------------------------------------------------------- the raw exchanges, and whether the judges' words agree

MAX_SHOWN = 8000  # characters of what a model was given (a deep dive's research runs much longer)
STOP = set(("the and for that with this its it's into from your their they them what which about more than have has "
            "been being were was are not but also only such very some most much many then when where while would could "
            "should there these those here just each even over under onto upon piece listener reflection deep dive").split())


def pieces(run: RunDir) -> dict:
    """What each contestant was given and what it wrote, by "model|track|item"."""
    out = {}
    for s in run.samples("samples"):
        given = s.get("input") or ""
        out[f"{s['model']}|{s['track']}|{s['passage']}"] = {
            "given": given[:MAX_SHOWN], "given_chars": len(given), "wrote": s["script"], "words": s.get("words"),
            "sources": s.get("sources", []), "seconds": s.get("seconds"), "usd": s.get("usd")}
    for c in run.samples("conversations"):
        out[f"{c['model']}|companion|{c['scenario']}"] = {
            "given": c["system"][-MAX_SHOWN:], "given_chars": len(c["system"]),
            "turns": [{"who": t["role"], "text": t["content"]} for t in c["turns"]], "seconds": c.get("seconds"), "usd": c.get("usd")}
    return out


def content_words(text: str) -> set[str]:
    import re
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in STOP}


def jaccard(a: set, b: set) -> float | None:
    return len(a & b) / len(a | b) if a | b else None


def text_agreement(rows: list[dict], judges: list[str]) -> dict:
    """For each piece: how far the judges' strengths (and weaknesses) share their words
    (mean pairwise Jaccard overlap of content words), and the words two or more used. A
    rough measure: two judges can say the same thing in different words, and similar
    words can hide different points; read the texts side by side to be sure."""
    by_piece = defaultdict(dict)
    for r in rows:
        by_piece["|".join(r["key"])][r["judge"]] = r
    out = {}
    for key, per in by_piece.items():
        entry = {}
        for field in ("strength", "weakness"):
            words = {j: content_words(per[j].get(field, "")) for j in per}
            pairs = [jaccard(words[a], words[b]) for a, b in combinations(sorted(words), 2)]
            pairs = [p for p in pairs if p is not None]
            counts = defaultdict(int)
            for ws in words.values():
                for w in ws:
                    counts[w] += 1
            entry[field] = {"overlap": r3(statistics.fmean(pairs)) if pairs else None,
                            "shared": sorted(w for w, n in counts.items() if n >= 2)}
        out[key] = entry
    return out


def main(args) -> None:
    run = RunDir(args.run)
    result = analyse(run, args.judges.split(","))
    out = Path(args.out) if args.out else WEB_OUT / f"{args.run}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    print(f"{out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", default="full")
    parser.add_argument("--judges", default="gemini,muse,llama-scout")
    parser.add_argument("--out", default=None)
    main(parser.parse_args())
