"""Scale study: which way of asking the judges gives scores worth having?

The current rubric (1 to 7, "4 is ordinary") piles nearly every score at 6 or 7, where
agreement statistics can't see anything. This study asks the same cheap and free judges
(Gemini 3.8 Flash, Muse Glimmer, Llama 4 Scout) to score the same pieces and
conversations in seven other ways, re-asks a quarter of them to measure stability, and
compares every way on:

- distribution shape: spread, values used, entropy, ceiling and floor, skew, kurtosis,
  and distance from a fitted normal curve;
- reliability between judges: Krippendorff's alpha, ICC(2,1), ICC(2,k), weighted kappa;
- discrimination: eta squared across the models (with a permutation p), and whether
  Claude Opus comes out on top again;
- test-retest: the same judge on the same item, asked twice;
- convergent validity: how each way's item scores correlate with the others';
- failures to return valid JSON, cost and time.

Variants (prompts in app/agent_prompts/eval_scale_*.md):
  v0         the current rubric (reused from evals.llm_judge, not re-asked)
  anchored   1-7 with every point described; 4 = typical competent work, most land 3-5
  ten        0-10 with distribution guidance: 5 is average, 9-10 exceptional
  exemplar   1-7 anchored to three reference pieces (weak, typical, strong) from other days
  critique   list specific flaws first, then score 1-7
  checklist  10 yes/no criteria per track, summed (0-10)
  pairwise   two pieces for the same day: which is better, by 0-3 (Bradley-Terry)
  ranking    all the pieces for a day ranked together (a forced distribution)

  .venv/bin/python -m evals.scale_study [--run full] [--collect] [--analyse]
Writes evals/runs/<run>/scale_study/ (cache) and ~/Code/ignatius-hw4-web/evals/scales.json.
"""

import argparse
import asyncio
import hashlib
import json
import math
import random
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import httpx
import numpy as np

from app import prompts

from .analysis import eta_squared, icc, weighted_kappa
from .common import (COMPANION_SCALES, MODELS, SCALES, ModelError, RunDir, chat, day_context,
                     goodness, load_passages, load_scenarios, parse_json, scripture)
from .reliability import alpha

JUDGES = ["gemini", "muse", "llama-scout"]
DIMS = {"piece": ["overall", "emotionally_engaging", "thoughtful", "too_vague", "ai_jargon"],
        "companion": ["overall", "listening", "spiritual_depth", "restraint"]}
LOWER = {"too_vague", "ai_jargon"}
# The primary score of every rubric variant (and of the current rubric) is the same
# composite, so they're compared like for like: the dimensions all of them ask about,
# lower-is-better ones turned around. "overall" is reported on its own.
COMPOSITE = {"piece": ["emotionally_engaging", "thoughtful", "too_vague", "ai_jargon"],
             "companion": ["listening", "spiritual_depth", "restraint"]}


def composite(scores: dict, track: str, lo: float, hi: float) -> float | None:
    dims = COMPOSITE["companion" if track == "companion" else "piece"]
    vals = [(lo + hi - scores[d]) if d in LOWER else scores[d] for d in dims if isinstance(scores.get(d), (int, float))]
    return statistics.fmean(vals) if len(vals) == len(dims) else None
VARIANTS = {
    "v0": {"label": "Current rubric (1–7)", "kind": "existing", "lo": 1, "hi": 7,
           "change": "The rubric as it is: 19 scales, 1–7, \"4 is ordinary, 1 and 7 are rare\"."},
    "anchored": {"label": "Anchored 1–7", "kind": "rubric", "lo": 1, "hi": 7,
                 "change": "Every point described; 4 = typical competent work; told most pieces land 3–5."},
    "ten": {"label": "0–10 with a target distribution", "kind": "rubric", "lo": 0, "hi": 10,
            "change": "Wider scale; 5 = average; told the expected shares at each end."},
    "exemplar": {"label": "Anchored to reference pieces", "kind": "rubric", "lo": 1, "hi": 7,
                 "change": "Three scored reference pieces (weak 2, typical 4, strong 6) from another day."},
    "critique": {"label": "Critique first, then 1–7", "kind": "rubric", "lo": 1, "hi": 7,
                 "change": "Must quote 3–5 specific flaws before scoring."},
    "checklist": {"label": "Checklist (10 yes/no)", "kind": "checklist", "lo": 0, "hi": 10,
                  "change": "Ten concrete yes/no criteria per track, summed; \"when in doubt, no\"."},
    "pairwise": {"label": "Pairwise comparison", "kind": "pairwise", "lo": -3, "hi": 3,
                 "change": "Two pieces for the same day: which is better, by 0–3; scored by Bradley–Terry."},
    "ranking": {"label": "Rank the day's pieces", "kind": "ranking", "lo": 1, "hi": 4,
                "change": "All the pieces for a day ranked together: a forced, even distribution."},
}
RETEST_SHARE = 4  # one item in four is asked twice
HERE = Path(__file__).resolve().parent
WEB_OUT = Path.home() / "Code" / "ignatius-hw4-web" / "evals" / "scales.json"
rng = random.Random(28)


def prompt_of(variant: str, section: str) -> str:
    text = (Path(prompts.PROMPT_DIR) / f"eval_scale_{variant}.md").read_text()
    parts = text.split("\n## ")
    for p in parts:
        head, _, body = p.lstrip("# ").partition("\n")
        if head.strip() == section:
            return body.strip()
    raise KeyError(f"{variant}: no section {section}")


def r3(x):
    return None if x is None or (isinstance(x, float) and x != x) else round(float(x), 3)


def retest(key: tuple) -> bool:
    return int(hashlib.md5("|".join(key).encode()).hexdigest(), 16) % RETEST_SHARE == 0


# ---------------------------------------------------------------- the items

class Items:
    """Every piece and conversation in the run, with what a judge needs to see."""

    def __init__(self, run: RunDir):
        self.run = run
        self.passages = {p["id"]: p for p in load_passages()}
        self.scenarios = {s["id"]: s for s in load_scenarios()}
        self.pieces = {(s["model"], s["track"], s["passage"]): s for s in run.samples("samples")}
        self.convs = {(c["model"], "companion", c["scenario"]): c for c in run.samples("conversations")}

    def keys(self) -> list[tuple]:
        return sorted(self.pieces) + sorted(self.convs)

    async def day(self, item: str) -> str:
        p = self.passages[item]
        return day_context(p, await scripture(p))

    @staticmethod
    def conversation(c: dict) -> str:
        return "\n\n".join(f"{'Person' if t['role'] == 'user' else 'Companion'}: {t['content']}" for t in c["turns"])

    async def shown(self, key: tuple) -> str:
        """What the judge sees for one item (research and the companion's long instructions left out:
        the dimensions studied don't need them, and it keeps the free judges quick)."""
        model, track, item = key
        if track == "companion":
            s = self.scenarios[item]
            return (f"Scenario: {s['when']} {s['last']} Day's passage: {self.passages[s['passage']]['ref']}.\n\n"
                    f"<conversation>\n{self.conversation(self.convs[key])}\n</conversation>")
        kind = "For the heart" if track == "heart" else "Deep dive"
        return f"Kind of piece: {kind}\n\n<day>\n{await self.day(item)}\n</day>\n\n<piece>\n{self.pieces[key]['script']}\n</piece>"

    def body(self, key: tuple) -> str:
        return self.conversation(self.convs[key]) if key[1] == "companion" else self.pieces[key]["script"]


def v0_scores(run: RunDir) -> dict:
    """{(judge, model, track, item): {"primary": composite, dims...}} from the existing judgments."""
    out = {}
    for f in (run.dir / "llm_judge").glob("*.json"):
        j = json.loads(f.read_text())
        if j["judge"] not in JUDGES:
            continue
        model, track, item = j["key"]
        scales = COMPANION_SCALES if track == "companion" else SCALES
        sc = {k: float(v) for k, v in j["scores"].items()}
        primary = composite(sc, track, 1, 7)
        if primary is None:
            continue
        entry = {"primary": primary, "all_scales": statistics.fmean(goodness(s, sc[s]) for s in scales if s in sc)}
        for d in DIMS["companion" if track == "companion" else "piece"]:
            if d in sc:
                entry[d] = sc[d]
        out[(j["judge"], model, track, item)] = entry
    return out


# ---------------------------------------------------------------- collecting

class Study:
    def __init__(self, run: RunDir):
        self.run = run
        self.items = Items(run)
        self.dir = run.dir / "scale_study"
        self.v0 = v0_scores(run)

    def path(self, variant: str, judge: str, key, rep: int) -> Path:
        name = "__".join([judge, *(key if isinstance(key, tuple) else (key,)), f"r{rep}"]).replace("/", "_")
        return self.dir / variant / f"{name}.json"

    async def ask(self, variant: str, judge: str, key, rep: int, system: str, user: str, valid) -> None:
        path = self.path(variant, judge, key, rep)
        if path.exists():
            return
        attempts, usd, seconds, parsed = 0, 0.0, 0.0, None
        for _ in range(2):
            attempts += 1
            try:
                reply = await chat(judge, system, user, json_reply=True)
            except (ModelError, httpx.HTTPError) as exc:
                seconds += 0
                last = str(exc)[:200]
                continue
            usd += reply["usd"]
            seconds += reply["seconds"]
            parsed = parse_json(reply["text"])
            if parsed is not None and valid(parsed):
                break
            parsed, last = None, "invalid JSON or missing fields"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"variant": variant, "judge": judge, "key": list(key) if isinstance(key, tuple) else key,
                                    "rep": rep, "attempts": attempts, "ok": parsed is not None, "result": parsed,
                                    "usd": round(usd, 5), "seconds": round(seconds, 1),
                                    **({} if parsed is not None else {"error": last})}, ensure_ascii=False))
        print(f"  {variant:9} {judge:11} {'|'.join(key if isinstance(key, tuple) else [str(key)])} r{rep} {'ok' if parsed else 'FAILED'}", flush=True)

    # --- rubric variants and the checklist: one item at a time
    def exemplars(self, key: tuple) -> str:
        """Weak, typical and strong reference pieces of the same track from another day,
        chosen by their current-rubric score averaged across judges."""
        model, track, item = key
        by = defaultdict(list)
        for (_j, m, t, it), v in self.v0.items():
            if t == track and it != item:
                by[(m, t, it)].append(v["primary"])
        ranked = sorted(by, key=lambda k: statistics.fmean(by[k]))
        if len(ranked) < 3:
            return ""
        picks = [("WEAK", 2, ranked[0]), ("TYPICAL", 4, ranked[len(ranked) // 2]), ("STRONG", 6, ranked[-1])]
        return "\n\n".join(f"<reference label=\"{label}\" overall=\"{score}\">\n{self.items.body(k)}\n</reference>" for label, score, k in picks)

    async def rubric_jobs(self, variant: str) -> list:
        jobs = []
        for key in self.items.keys():
            section = "companion" if key[1] == "companion" else "pieces"
            if variant == "checklist":
                section = {"heart": "heart", "deep": "deep", "companion": "companion"}[key[1]]
            system = prompt_of(variant, section)
            user = await self.items.shown(key)
            if variant == "exemplar":
                user = f"<references>\n{self.exemplars(key)}\n</references>\n\nNow the one to rate:\n\n{user}"
            dims = DIMS["companion" if key[1] == "companion" else "piece"]
            if variant == "checklist":
                valid = lambda r: isinstance(r.get("answers"), dict) and all(f"c{i}" in r["answers"] for i in range(1, 11))
            else:
                valid = (lambda dims: lambda r: isinstance(r.get("scores"), dict)
                         and all(isinstance(r["scores"].get(d), (int, float)) for d in dims))(dims) # noqa: B023 (bound by the call)
            for judge in JUDGES:
                for rep in (1, 2) if retest(key) else (1,):
                    jobs.append(self.ask(variant, judge, key, rep, system, user, valid))
        return jobs

    # --- pairwise: every pair of models on the same passage and track
    def groups(self) -> dict:
        g = defaultdict(list)
        for key in self.items.keys():
            g[(key[1], key[2])].append(key)
        return g

    async def pairwise_jobs(self) -> list:
        jobs = []
        for (track, item), keys in self.groups().items():
            section = "companion" if track == "companion" else "pieces"
            system = prompt_of("pairwise", section)
            for a, b in combinations(sorted(keys), 2):
                pair_key = (track, item, a[0], b[0])
                first, second = (a, b) if rng.random() < 0.5 else (b, a)  # random order, recorded
                if track == "companion":
                    s = self.items.scenarios[item]
                    head = f"Scenario: {s['when']} Day's passage: {self.items.passages[s['passage']]['ref']}."
                else:
                    head = f"Kind of piece: {'For the heart' if track == 'heart' else 'Deep dive'}\n\n<day>\n{await self.items.day(item)}\n</day>"
                user = (f"{head}\n\n<A>\n{self.items.body(first)}\n</A>\n\n<B>\n{self.items.body(second)}\n</B>")
                valid = lambda r: r.get("better") in ("A", "B", "same") and isinstance(r.get("margin"), (int, float))
                for judge in JUDGES:
                    for rep in (1, 2) if retest(pair_key) else (1,):
                        jobs.append(self.ask_ordered("pairwise", judge, pair_key, rep, system, user, valid, first[0], second[0]))
        return jobs

    async def ask_ordered(self, variant, judge, key, rep, system, user, valid, a_model, b_model):
        await self.ask(variant, judge, key, rep, system, user, valid)
        path = self.path(variant, judge, key, rep)
        rec = json.loads(path.read_text())
        if "order" not in rec:
            rec["order"] = [a_model, b_model]
            path.write_text(json.dumps(rec, ensure_ascii=False))

    # --- ranking: all the pieces for a day together
    async def ranking_jobs(self) -> list:
        jobs = []
        for (track, item), keys in self.groups().items():
            if len(keys) < 3:
                continue
            section = "companion" if track == "companion" else "pieces"
            system = prompt_of("ranking", section)
            order = sorted(keys)
            rng.shuffle(order)
            labels = {f"P{i + 1}": k for i, k in enumerate(order)}
            head = (f"Kind of piece: {'For the heart' if track == 'heart' else 'Deep dive' if track == 'deep' else 'Companion conversation'}")
            if track != "companion":
                head += f"\n\n<day>\n{await self.items.day(item)}\n</day>"
            user = head + "\n\n" + "\n\n".join(f"<{lab}>\n{self.items.body(k)}\n</{lab}>" for lab, k in labels.items())
            valid = (lambda labs: lambda r: isinstance(r.get("ranking"), list) and sorted(r["ranking"]) == sorted(labs))(list(labels))
            group_key = (track, item)
            for judge in JUDGES:
                for rep in (1, 2) if retest(group_key) else (1,):
                    jobs.append(self.ask_labelled(judge, group_key, rep, system, user, valid, labels))
        return jobs

    async def ask_labelled(self, judge, key, rep, system, user, valid, labels):
        await self.ask("ranking", judge, key, rep, system, user, valid)
        path = self.path("ranking", judge, key, rep)
        rec = json.loads(path.read_text())
        if "labels" not in rec:
            rec["labels"] = {lab: k[0] for lab, k in labels.items()}
            path.write_text(json.dumps(rec, ensure_ascii=False))

    async def collect(self) -> None:
        jobs = []
        for v in ("anchored", "ten", "exemplar", "critique", "checklist"):
            jobs += await self.rubric_jobs(v)
        jobs += await self.pairwise_jobs()
        jobs += await self.ranking_jobs()
        print(f"{len(jobs)} judgments to make (cached ones are skipped)", flush=True)
        await asyncio.gather(*jobs)


# ---------------------------------------------------------------- scoring each variant

def bradley_terry(wins: dict, players: list[str], rounds: int = 200) -> dict:
    """Bradley-Terry strengths from {(winner, loser): weight} by the MM algorithm, as log-strengths centred on 0."""
    p = {m: 1.0 for m in players}
    w = defaultdict(float)
    n = defaultdict(float)
    for (a, b), c in wins.items():
        w[a] += c
        n[frozenset((a, b))] += c
    for _ in range(rounds):
        new = {}
        for i in players:
            denom = sum(n[frozenset((i, j))] / (p[i] + p[j]) for j in players if j != i and n[frozenset((i, j))])
            new[i] = (w[i] + 0.1) / denom if denom else p[i]
        g = math.exp(statistics.fmean(math.log(v) for v in new.values()))
        p = {k: v / g for k, v in new.items()}
    return {k: r3(math.log(v)) for k, v in p.items()}


def load_variant(study: Study, variant: str) -> list[dict]:
    d = study.dir / variant
    return [json.loads(f.read_text()) for f in sorted(d.glob("*.json"))] if d.exists() else []


def scores_for(study: Study, variant: str) -> tuple[dict, dict, dict]:
    """(first-run scores, second-run scores, bookkeeping). Scores map
    (judge, model, track, item) -> {"primary": x, dim: x...}; primary is the variant's
    overall judgment on its own scale (higher is always better)."""
    info = {"calls": 0, "attempts": 0, "failed": 0, "usd": 0.0, "seconds": 0.0, "position_A": [], "wins": defaultdict(lambda: defaultdict(float))}
    runs = {1: {}, 2: {}}
    if variant == "v0":
        return study.v0, {}, info
    recs = load_variant(study, variant)
    for rec in recs:
        info["calls"] += 1
        info["attempts"] += rec["attempts"]
        info["failed"] += not rec["ok"]
        info["usd"] += rec["usd"]
        info["seconds"] += rec["seconds"]
    kind = VARIANTS[variant]["kind"]
    if kind in ("rubric", "checklist"):
        for rec in recs:
            if not rec["ok"]:
                continue
            judge, (model, track, item) = rec["judge"], rec["key"]
            if kind == "checklist":
                ans = rec["result"]["answers"]
                entry = {"primary": float(sum(bool(ans.get(f"c{i}")) for i in range(1, 11))),
                         "criteria": {f"c{i}": bool(ans.get(f"c{i}")) for i in range(1, 11)}}
            else:
                sc = {k: float(v) for k, v in rec["result"]["scores"].items() if isinstance(v, (int, float))}
                primary = composite(sc, track, VARIANTS[variant]["lo"], VARIANTS[variant]["hi"])
                if primary is None:
                    continue
                entry = {"primary": primary, **sc}
            runs[rec["rep"]][(judge, model, track, item)] = entry
    elif kind == "pairwise":
        signed = {1: defaultdict(list), 2: defaultdict(list)}
        for rec in recs:
            if not rec["ok"] or "order" not in rec:
                continue
            track, item, _, _ = rec["key"]
            a, b = rec["order"]
            r = rec["result"]
            m = max(0, min(3, float(r["margin"])))
            s = 0.0 if r["better"] == "same" or m == 0 else (m if r["better"] == "A" else -m)
            info["position_A"].append(1 if s > 0 else 0 if s < 0 else 0.5)
            signed[rec["rep"]][(rec["judge"], a, track, item)].append(s)
            signed[rec["rep"]][(rec["judge"], b, track, item)].append(-s)
            if rec["rep"] == 1:
                winner, loser = (a, b) if s > 0 else (b, a)
                if s == 0:
                    info["wins"][track][(a, b)] += 0.5
                    info["wins"][track][(b, a)] += 0.5
                else:
                    info["wins"][track][(winner, loser)] += 1 + abs(s) / 3  # a clear win counts more
        for rep in (1, 2):
            runs[rep] = {k: {"primary": statistics.fmean(v)} for k, v in signed[rep].items()}
    elif kind == "ranking":
        for rec in recs:
            if not rec["ok"] or "labels" not in rec:
                continue
            track, item = rec["key"]
            order = rec["result"]["ranking"]
            n = len(order)
            for pos, lab in enumerate(order):
                runs[rec["rep"]][(rec["judge"], rec["labels"][lab], track, item)] = {"primary": float(n - pos)}  # n = best
    return runs[1], runs[2], info


# ---------------------------------------------------------------- statistics

def shape(values: list[float], lo: float, hi: float, discrete: bool) -> dict:
    if len(values) < 3:
        return {}
    x = np.array(values, dtype=float)
    span = hi - lo
    sd = float(x.std())
    mean = float(x.mean())
    if discrete:
        cats = np.arange(lo, hi + 1)
        counts = np.array([(np.round(x) == c).sum() for c in cats], dtype=float)
    else:
        edges = np.linspace(lo, hi, 8)
        counts, _ = np.histogram(x, bins=edges)
        counts = counts.astype(float)
    p = counts / counts.sum()
    nz = p[p > 0]
    entropy = float(-(nz * np.log(nz)).sum() / math.log(len(p))) if len(p) > 1 else 0.0
    # distance from a normal curve with this mean and spread, over the same bins
    if sd > 0:
        from math import erf, sqrt
        cdf = lambda v: 0.5 * (1 + erf((v - mean) / (sd * sqrt(2))))
        if discrete:
            bounds = [(c - 0.5, c + 0.5) for c in cats]
        else:
            e = np.linspace(lo, hi, 8)
            bounds = list(zip(e[:-1], e[1:], strict=False))
        expected = np.array([cdf(b) - cdf(a) for a, b in bounds])
        expected = expected / expected.sum() if expected.sum() else expected
        tv = float(0.5 * np.abs(p - expected).sum())
    else:
        tv = 1.0
    skew = float(((x - mean) ** 3).mean() / sd ** 3) if sd else 0.0
    kurt = float(((x - mean) ** 4).mean() / sd ** 4 - 3) if sd else 0.0
    top = hi - span * 2 / 7  # the top two points of a 7-point scale, and the same share of any other
    bottom = lo + span * 2 / 7
    return {"n": len(values), "mean": r3(mean), "sd": r3(sd), "sd_norm": r3(sd / span), "distinct": int(len(np.unique(np.round(x, 2)))),
            "entropy": r3(entropy), "ceiling": r3(float((x > top).mean())), "floor": r3(float((x < bottom).mean())),
            "skew": r3(skew), "kurtosis": r3(kurt), "normal_tv": r3(tv), "hist": [int(c) for c in counts],
            "hist_labels": [str(int(c)) if discrete else f"{a:.1f}" for c, a in zip(cats if discrete else np.linspace(lo, hi, 8)[:-1], np.linspace(lo, hi, 8)[:-1], strict=False)]}


def reliability(first: dict, lo: float, hi: float, discrete: bool) -> dict:
    """Within each track (so telling a deep dive from a conversation earns nothing), then
    averaged over the tracks, weighted by their items."""
    per = {}
    for track in ("heart", "deep", "companion"):
        sub = {k: v for k, v in first.items() if k[2] == track}
        if len({k[1:] for k in sub}) >= 4:
            per[track] = _reliability(sub, lo, hi, discrete)
    out = {"by_track": per}
    for key in ("alpha", "icc1", "icck", "kappa", "rho"):
        vals = [(r[key], r["items"]) for r in per.values() if r.get(key) is not None]
        out[key] = r3(sum(v * n for v, n in vals) / sum(n for _, n in vals)) if vals else None
    out["items"] = sum(r["items"] for r in per.values())
    return out


def _reliability(first: dict, lo: float, hi: float, discrete: bool) -> dict:
    items = sorted({k[1:] for k in first})
    judges = [j for j in JUDGES if any(k[0] == j for k in first)]
    units = [[first[(j, *it)]["primary"] for j in judges if (j, *it) in first] for it in items]
    complete = [[first[(j, *it)]["primary"] for j in judges] for it in items if all((j, *it) in first for j in judges)]
    one, avg = icc(np.array(complete)) if len(complete) >= 3 else (None, None)
    kappas = []
    if discrete:
        k = int(hi - lo + 1)
        for a, b in combinations(judges, 2):
            pa = [(first[(a, *it)]["primary"], first[(b, *it)]["primary"]) for it in items if (a, *it) in first and (b, *it) in first]
            if len(pa) >= 5:
                xs = [int(round(v - lo)) + 1 for v, _ in pa]
                ys = [int(round(v - lo)) + 1 for _, v in pa]
                kap = weighted_kappa(xs, ys, k)
                if kap is not None:
                    kappas.append(kap)
    rhos = []  # rank agreement, blind to one judge simply being more lenient than another
    for a, b in combinations(judges, 2):
        pa = [(first[(a, *it)]["primary"], first[(b, *it)]["primary"]) for it in items if (a, *it) in first and (b, *it) in first]
        if len(pa) >= 5:
            try:
                rhos.append(statistics.correlation(*zip(*pa, strict=False), method="ranked"))
            except statistics.StatisticsError:
                pass
    return {"alpha": r3(alpha(units)), "icc1": r3(one), "icck": r3(avg), "kappa": r3(statistics.fmean(kappas)) if kappas else None,
            "rho": r3(statistics.fmean(rhos)) if rhos else None,
            "items": len(items), "complete": len(complete)}


def item_means(first: dict) -> dict:
    acc = defaultdict(list)
    for (_j, m, t, it), v in first.items():
        acc[(m, t, it)].append(v["primary"])
    return {k: statistics.fmean(v) for k, v in acc.items()}


def discrimination(means: dict) -> dict:
    out = {}
    for track in ("heart", "deep", "companion"):
        sub = {k: v for k, v in means.items() if k[1] == track}
        if len(sub) < 6:
            continue
        eta, p, null = eta_squared(sub)
        by = defaultdict(list)
        for k, v in sub.items():
            by[k[0]].append(v)
        model_means = {m: r3(statistics.fmean(v)) for m, v in by.items()}
        order = sorted(model_means, key=lambda m: -model_means[m])
        out[track] = {"eta2": r3(eta), "p": r3(p), "null": r3(null), "model_means": model_means, "order": order,
                      "opus_top": order[0] == "opus" if order else None}
    return out


def test_retest(first: dict, second: dict) -> dict:
    pairs = [(first[k]["primary"], second[k]["primary"]) for k in second if k in first]
    if len(pairs) < 5:
        return {"n": len(pairs)}
    x, y = zip(*pairs, strict=False)
    try:
        rho = statistics.correlation(x, y, method="ranked")
    except statistics.StatisticsError:
        rho = None
    one, _ = icc(np.array(pairs))
    return {"n": len(pairs), "spearman": r3(rho), "icc": r3(one), "exact": r3(statistics.fmean(a == b for a, b in pairs)),
            "mean_abs_diff": r3(statistics.fmean(abs(a - b) for a, b in pairs))}


def analyse(study: Study) -> dict:
    results, means_by_variant = {}, {}
    for v, meta in VARIANTS.items():
        first, second, info = scores_for(study, v)
        if not first:
            continue
        discrete = meta["kind"] in ("checklist", "ranking")  # rubric scores are composites of several dimensions
        lo, hi = meta["lo"], meta["hi"]
        values = [e["primary"] for e in first.values()]
        per_judge = {j: shape([e["primary"] for k, e in first.items() if k[0] == j], lo, hi, discrete) for j in JUDGES}
        dims = {}
        if meta["kind"] in ("rubric", "existing"):
            for d in sorted({d for e in first.values() for d in e if d not in ("primary", "criteria")}):
                sub = {k: {"primary": e[d]} for k, e in first.items() if d in e}
                dims[d] = {"shape": shape([e["primary"] for e in sub.values()], lo, hi, True),
                           "reliability": reliability(sub, lo, hi, True), "lower_is_better": d in LOWER}
        criteria = {}
        if meta["kind"] == "checklist":
            for c in (f"c{i}" for i in range(1, 11)):
                for track in ("heart", "deep", "companion"):
                    vals = [e["criteria"][c] for k, e in first.items() if k[2] == track]
                    if vals:
                        criteria.setdefault(track, {})[c] = r3(statistics.fmean(vals))
        means = item_means(first)
        means_by_variant[v] = means
        bt = {t: bradley_terry(w, sorted({m for pair in w for m in pair})) for t, w in info["wins"].items()} if info["wins"] else None
        results[v] = {
            "label": meta["label"], "kind": meta["kind"], "change": meta["change"], "range": [lo, hi],
            "shape": shape(values, lo, hi, discrete), "per_judge": per_judge,
            "reliability": reliability(first, lo, hi, meta["kind"] in ("rubric", "existing", "checklist")),
            "discrimination": discrimination(means), "test_retest": test_retest(first, second),
            "dimensions": dims, "criteria": criteria, "bradley_terry": bt,
            "position_bias_A": r3(statistics.fmean(info["position_A"])) if info["position_A"] else None,
            "calls": info["calls"], "failed": info["failed"], "attempts": info["attempts"],
            "failure_rate": r3(info["failed"] / info["calls"]) if info["calls"] else None,
            "retry_rate": r3((info["attempts"] - info["calls"]) / info["calls"]) if info["calls"] else None,
            "usd": r3(info["usd"]), "seconds": r3(info["seconds"]),
            "items": {"|".join(k): r3(v) for k, v in means.items()},
            "judgments": [{"judge": k[0], "model": k[1], "track": k[2], "item": k[3], "score": r3(e["primary"])} for k, e in first.items()],
        }
    # convergent validity: Spearman correlation of item means between every two variants
    names = list(means_by_variant)
    conv = {}
    for a in names:
        for b in names:
            common = [k for k in means_by_variant[a] if k in means_by_variant[b]]
            if a == b:
                conv[f"{a}|{b}"] = 1.0
            elif len(common) >= 5:
                try:
                    conv[f"{a}|{b}"] = r3(statistics.correlation([means_by_variant[a][k] for k in common],
                                                                 [means_by_variant[b][k] for k in common], method="ranked"))
                except statistics.StatisticsError:
                    conv[f"{a}|{b}"] = None
    for v in results:
        others = [conv.get(f"{v}|{o}") for o in names if o != v and conv.get(f"{v}|{o}") is not None]
        results[v]["convergent_mean"] = r3(statistics.fmean(others)) if others else None
    ranking = rank_variants(results)
    return {"made": datetime.now(timezone.utc).isoformat(timespec="minutes"), "run": study.run.dir.name, "judges": JUDGES,
            "judge_labels": {j: MODELS[j][2] for j in JUDGES}, "model_labels": {m: MODELS[m][2] for m in MODELS},
            "variants": results, "convergent": {"names": names, "matrix": [[conv.get(f"{a}|{b}") for b in names] for a in names]},
            "ranking": ranking}


# The criteria a good scale should meet, each scored so that higher is better, then ranked.
CRITERIA = {
    "spread": ("Spread (normalized SD)", lambda r: r["shape"].get("sd_norm")),
    "entropy": ("Values used (entropy)", lambda r: r["shape"].get("entropy")),
    "no_ceiling": ("Not piled at the top", lambda r: None if r["shape"].get("ceiling") is None else 1 - r["shape"]["ceiling"]),
    "normal": ("Close to a bell curve", lambda r: None if r["shape"].get("normal_tv") is None else 1 - r["shape"]["normal_tv"]),
    "agreement": ("Judges agree (ICC(2,k))", lambda r: r["reliability"].get("icck")),
    "alpha": ("Krippendorff's alpha", lambda r: r["reliability"].get("alpha")),
    "retest": ("Same answer twice (retest ρ)", lambda r: r["test_retest"].get("spearman")),
    "discrimination": ("Separates the models (η² above chance)", lambda r: _eta_margin(r)),
    "valid_json": ("Returns valid JSON", lambda r: None if r.get("failure_rate") is None else 1 - r["failure_rate"]),
}


def _eta_margin(r):
    vals = [(d["eta2"] or 0) - (d["null"] or 0) for d in r["discrimination"].values() if d.get("eta2") is not None]
    return statistics.fmean(vals) if vals else None


def rank_variants(results: dict) -> dict:
    table = {}
    for key, (label, f) in CRITERIA.items():
        vals = {v: f(r) for v, r in results.items()}
        present = sorted([v for v in vals if vals[v] is not None], key=lambda v: -vals[v])
        table[key] = {"label": label, "values": {v: r3(vals[v]) for v in vals}, "rank": {v: i + 1 for i, v in enumerate(present)}}
    mean_rank = {}
    for v in results:
        ranks = [table[k]["rank"][v] for k in table if v in table[k]["rank"]]
        mean_rank[v] = r3(statistics.fmean(ranks)) if ranks else None
    order = sorted(mean_rank, key=lambda v: mean_rank[v] if mean_rank[v] is not None else 99)
    return {"criteria": table, "mean_rank": mean_rank, "order": order}


def main(args) -> None:
    study = Study(RunDir(args.run))
    if args.collect or not args.analyse:
        started = time.time()
        asyncio.run(study.collect())
        print(f"collected in {time.time() - started:.0f} s")
    if args.analyse or not args.collect:
        result = analyse(study)
        WEB_OUT.parent.mkdir(parents=True, exist_ok=True)
        WEB_OUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        study.dir.mkdir(parents=True, exist_ok=True)
        (study.dir / "scales.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
        print(f"{WEB_OUT} ({WEB_OUT.stat().st_size // 1024} KB); order: {result['ranking']['order']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", default="full")
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--analyse", action="store_true")
    main(parser.parse_args())


# ---------------------------------------------------------------- the report's tables

def f2(x):
    return "–" if x is None else f"{x:.2f}"


def pct(x):
    return "–" if x is None else f"{round(x * 100)}%"


def report_tables(result: dict) -> str:
    """The numeric part of docs/evals/SCALE_STUDY.md, built from the analysis."""
    V = result["variants"]
    order = result["ranking"]["order"]
    rank = result["ranking"]
    lines = ["## Ranking", "",
             "Each variant ranked on nine criteria (1 = best); the table is ordered by the mean rank.", "",
             "| Rank | Variant | Mean rank | " + " | ".join(c["label"] for c in rank["criteria"].values()) + " |",
             "| --- | --- | --- | " + " | ".join("---" for _ in rank["criteria"]) + " |"]
    for i, v in enumerate(order, 1):
        cells = [str(rank["criteria"][k]["rank"].get(v, "–")) for k in rank["criteria"]]
        lines.append(f"| {i} | {V[v]['label']} | {f2(rank['mean_rank'][v])} | " + " | ".join(cells) + " |")
    lines += ["", "## The numbers", "",
              "| Variant | What changed | SD (share of range) | Entropy | Ceiling | Skew | Distance from normal | α (within track) | ICC(2,1) | ICC(2,k) | κw | Judge–judge ρ | Retest ρ | η² − chance (mean of tracks) | Opus on top | Invalid JSON | Cost | Judge-minutes |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for v in order:
        r = V[v]
        sh, rl, tr = r["shape"], r["reliability"], r["test_retest"]
        tops = [d.get("opus_top") for d in r["discrimination"].values() if d.get("opus_top") is not None]
        lines.append(f"| {r['label']} | {r['change']} | {f2(sh.get('sd_norm'))} | {f2(sh.get('entropy'))} | {pct(sh.get('ceiling'))} | "
                     f"{f2(sh.get('skew'))} | {f2(sh.get('normal_tv'))} | {f2(rl.get('alpha'))} | {f2(rl.get('icc1'))} | {f2(rl.get('icck'))} | "
                     f"{f2(rl.get('kappa'))} | {f2(rl.get('rho'))} | {f2(tr.get('spearman'))} | {f2(_eta_margin(r))} | {sum(tops)}/{len(tops)} | "
                     f"{pct(r.get('failure_rate'))} | ${r.get('usd') or 0:.2f} | {round((r.get('seconds') or 0) / 60)} |")
    lines += ["", "### Separating the models, track by track", "",
              "| Variant | Track | η² | chance η² | p | Order (best first) |", "| --- | --- | --- | --- | --- | --- |"]
    for v in order:
        for t, d in V[v]["discrimination"].items():
            lines.append(f"| {V[v]['label']} | {t} | {f2(d['eta2'])} | {f2(d['null'])} | {f2(d['p'])} | "
                         + " > ".join(result['model_labels'].get(m, m).split(' (')[0] for m in d["order"]) + " |")
    names = result["convergent"]["names"]
    lines += ["", "### Convergent validity (Spearman ρ between variants' item scores)", "",
              "| | " + " | ".join(V[n]["label"] for n in names) + " |", "| --- |" + " --- |" * len(names)]
    for a, row in zip(names, result["convergent"]["matrix"], strict=False):
        lines.append(f"| {V[a]['label']} | " + " | ".join(f2(x) for x in row) + " |")
    lines += ["", "### Each judge's distribution (primary score)", "",
              "| Variant | Judge | SD (share of range) | Entropy | Ceiling | Mean |", "| --- | --- | --- | --- | --- | --- |"]
    for v in order:
        for j, sh in V[v]["per_judge"].items():
            if sh:
                lines.append(f"| {V[v]['label']} | {result['judge_labels'][j].split(' (')[0]} | {f2(sh.get('sd_norm'))} | {f2(sh.get('entropy'))} | {pct(sh.get('ceiling'))} | {f2(sh.get('mean'))} |")
    dims = [(v, d, x) for v in order for d, x in V[v]["dimensions"].items()]
    if dims:
        lines += ["", "### Single dimensions (rubric variants)", "",
                  "| Variant | Dimension | SD (share of range) | Ceiling | α | ICC(2,k) |", "| --- | --- | --- | --- | --- | --- |"]
        for v, d, x in dims:
            lines.append(f"| {V[v]['label']} | {d.replace('_', ' ')}{' ↓' if x['lower_is_better'] else ''} | {f2(x['shape'].get('sd_norm'))} | "
                         f"{pct(x['shape'].get('ceiling'))} | {f2(x['reliability'].get('alpha'))} | {f2(x['reliability'].get('icck'))} |")
    if V.get("checklist", {}).get("criteria"):
        lines += ["", "### Checklist: share of items meeting each criterion", ""]
        for t, cs in V["checklist"]["criteria"].items():
            lines.append(f"- **{t}**: " + ", ".join(f"{c} {pct(p)}" for c, p in cs.items()))
    if V.get("pairwise", {}).get("bradley_terry"):
        lines += ["", "### Bradley–Terry strengths from the pairwise comparisons (log scale, 0 = average)", ""]
        for t, bt in V["pairwise"]["bradley_terry"].items():
            lines.append(f"- **{t}**: " + ", ".join(f"{result['model_labels'].get(m, m).split(' (')[0]} {f2(s)}" for m, s in sorted(bt.items(), key=lambda kv: -kv[1])))
        lines.append(f"- Position bias: the first-shown piece won {pct(V['pairwise']['position_bias_A'])} of comparisons (50% = none).")
    return "\n".join(lines)
