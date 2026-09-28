"""Fitness for this app: which model to use, and at what cost, measured mostly without
asking another model's opinion.

Reads the pieces in evals/runs/<run>/ (samples, conversations) and the cheap judges'
scores (llm_judge) for one axis only, and writes docs-ready numbers to the web app's
evals/fitness.json. Everything else is computed from the text itself:

- Scripture fidelity: quoted spans checked against the World English Bible passage.
- Grounding (deep dives): sources inside the research given; a claim-support proxy.
- Instruction following: length against target, spoken format, reply tags, the grace.
- Written for the ear: sentence length and Flesch reading ease.
- Companion behaviour: one question at a time, reply length, 988 in the at-risk
  scenario, no claim to be a spiritual director.
- Operations: missing or empty pieces, latency (median, p90), cost per piece, and a
  projected text cost per 7-day retreat.
- Quality: the cheap judges' mean overall score (the only judge-based axis).
- Value: a composite fitness score under several weightings, a random-weights
  sensitivity analysis, quality per dollar, and Pareto frontiers of cost vs quality.

No paid calls. Run from the repository root:
  .venv/bin/python -m evals.fitness [--run full] [--out PATH]
The method and the JSON's fields are documented in docs/evals/FITNESS.md.
"""

import argparse
import json
import math
import random
import re
import statistics
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from .common import COMPANION_SCALES, HERE, MODELS, SCALES, goodness

WEB_OUT = Path.home() / "Code" / "ignatius-hw4-web" / "evals" / "fitness.json"
RETREAT_DAYS = 7
BOOTSTRAP = 2000
rng = random.Random(7)

# Projected text cost of a retreat day, from what was measured here. The eval measured the
# reflection ("heart") and the deep dive; the other text calls are estimated from them:
GUIDE_FACTOR = 0.6   # tailoring the spoken guidance: reads the day and both scripts, writes short lines
PLAN_FACTOR = 1.0    # planning, once per retreat: about one deep dive's worth (a long read, a long JSON reply)
CLAUDE_SEARCH_USD = 0.05  # in the app Claude also searches the web itself (up to 5 x $0.01); the eval turned it off

DIMS = [
    ("quality", "Judged quality", "Cheap judges' mean overall score (1-7), rescaled to 0-1."),
    ("fidelity", "Scripture fidelity", "Share of quotations from the passage that are word for word."),
    ("grounding", "Grounding", "Deep dives: sources inside the research given, and dated or original-language claims whose key tokens appear in the research."),
    ("instructions", "Follows instructions", "Length within 15% of target, no markdown or lists, reply tags present, the day's grace echoed (heart)."),
    ("ear", "Written for the ear", "Mean sentence length and Flesch reading ease within the range that reads well aloud."),
    ("companion", "Companion behaviour", "At most one question a turn, replies of speakable length, 988 named when the person is at risk, no claim to be a spiritual director."),
    ("reliability", "Reliability", "Share of expected pieces produced and non-empty."),
    ("speed", "Speed", "Median seconds per piece, on a log scale (5 s = 1, 120 s = 0)."),
    ("cost", "Cost", "Projected text cost of a 7-day retreat: 1 / (1 + dollars)."),
]
WEIGHTS = {
    "balanced": {"quality": .25, "fidelity": .15, "grounding": .10, "instructions": .10, "ear": .10, "companion": .10,
                 "reliability": .05, "speed": .05, "cost": .10},
    "fidelity_first": {"quality": .15, "fidelity": .35, "grounding": .20, "instructions": .10, "ear": .05, "companion": .05,
                       "reliability": .05, "speed": .025, "cost": .025},
    "cost_first": {"quality": .20, "fidelity": .10, "grounding": .05, "instructions": .05, "ear": .05, "companion": .05,
                   "reliability": .05, "speed": .05, "cost": .40},
    "speaking_first": {"quality": .25, "fidelity": .10, "grounding": .0, "instructions": .15, "ear": .25, "companion": .15,
                       "reliability": .0, "speed": .05, "cost": .05},
    "companion_first": {"quality": .20, "fidelity": .0, "grounding": .0, "instructions": .0, "ear": .10, "companion": .35,
                        "reliability": .05, "speed": .20, "cost": .10},
}


def r3(x):
    return None if x is None or (isinstance(x, float) and x != x) else round(float(x), 3)


def boot(values: list[float]) -> tuple:
    values = [v for v in values if v is not None]
    if not values:
        return None, None, None
    if len(values) == 1:
        return values[0], None, None
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(BOOTSTRAP))
    return statistics.fmean(values), means[int(.025 * BOOTSTRAP)], means[int(.975 * BOOTSTRAP)]


# ---------------------------------------------------------------- text helpers

def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).lower()
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z’']+", text)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])[\"”’)]*\s+", text) if len(s.split()) >= 2]


def syllables(word: str) -> int:
    w = word.lower().strip("'’")
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", w)))


def flesch(text: str) -> float | None:
    sents, ws = sentences(text), words(text)
    if not sents or not ws:
        return None
    return 206.835 - 1.015 * (len(ws) / len(sents)) - 84.6 * (sum(syllables(w) for w in ws) / len(ws))


# ---------------------------------------------------------------- 1. scripture fidelity

def quoted_spans(text: str) -> list[str]:
    """Quotations, paired in order: curly quotes open and close; a straight quote toggles.
    (Pairing matters: a short quote like "Mary." must not swallow the text after it.)"""
    spans, start = [], None
    for m in re.finditer(r"[“”\"]", text):
        ch = m.group(0)
        if ch == "“" or (ch == '"' and start is None):
            start = m.end()
        elif start is not None:
            spans.append(text[start:m.start()])
            start = None
    return [sp.strip() for sp in spans if 0 < len(sp) <= 500]


def best_match(span: str, passage: str) -> float:
    """Similarity (0-1) of a quoted span to the closest same-length stretch of the passage."""
    s, p = norm(span).split(), norm(passage).split()
    if not s or not p:
        return 0.0
    n, best = len(s), 0.0
    target = " ".join(s)
    for size in {max(1, n - 2), n, n + 2}:
        for i in range(0, max(1, len(p) - size + 1)):
            r = SequenceMatcher(None, target, " ".join(p[i:i + size]), autojunk=False).ratio()
            if r > best:
                best = r
                if best == 1.0:
                    return best
    return best


def scripture_fidelity(script: str, passage: str) -> dict:
    """Each quotation of 4+ words: verbatim (in the passage exactly), close (a tiny slip),
    misquote (clearly this passage, but altered), or other (not from this passage: another
    text, a tradition, or the writer's own words in quotation marks; not judged here)."""
    out = {"quotes": 0, "verbatim": 0, "close": 0, "misquote": 0, "other": 0, "examples": []}
    np_ = f" {norm(passage)} "
    for span in quoted_spans(script):
        n = len(norm(span).split())
        if n < 4:
            continue
        out["quotes"] += 1
        if f" {norm(span)} " in np_ or norm(span) in np_:
            out["verbatim"] += 1
            continue
        r = best_match(span, passage)
        # A misquote must be long enough to be clearly this passage (short phrases match by chance).
        kind = "close" if r >= 0.93 else "misquote" if r >= 0.75 and n >= 6 else "other"
        out[kind] += 1
        if kind != "close" and len(out["examples"]) < 3:
            out["examples"].append({"kind": kind, "quote": span[:200], "similarity": r3(r)})
    from_passage = out["verbatim"] + out["close"] + out["misquote"]
    out["from_passage"] = from_passage
    out["fidelity"] = r3((out["verbatim"] + out["close"]) / from_passage) if from_passage else None
    return out


# ---------------------------------------------------------------- 2. grounding

def url_key(u: str) -> str:
    u = re.sub(r"^https?://(www\.)?", "", (u or "").strip().lower())
    return u.rstrip("/.,)")


NAMED_WORD = re.compile(r"\b(?:Greek|Hebrew|Aramaic|Latin)(?: word| verb| noun| term| phrase)?,? (?:is |here is |translated )?[\"“']?([a-zA-Zāēīōūḥṣṭ]{3,})", re.I)
CLAIM_TOKEN = re.compile(r"\b(\d{2,4})\b|([Ͱ-Ͽἀ-῿֐-׿]+)|\b(?:BCE?|AD|CE)\b|\b([a-z]*[āēīōūḥṣṭ][a-z]*)\b", re.I)


def grounding(sample: dict, research: dict) -> dict:
    given = {url_key(r["url"]) for r in research.get("results", [])}
    corpus = norm(" ".join(f"{r.get('title', '')} {r.get('content', '')}" for r in research.get("results", [])))
    cited = [url_key(u) for s in sample.get("sources", []) for u in re.findall(r"https?://\S+", s)] or \
        [url_key(s) for s in sample.get("sources", []) if s.startswith("http")]
    inside = [c for c in cited if c in given or any(c.startswith(g) or g.startswith(c) for g in given)]
    claims, supported = 0, 0
    examples = []
    for sent in sentences(sample["script"]):
        toks = [t for m in CLAIM_TOKEN.finditer(sent) for t in m.groups() if t] + NAMED_WORD.findall(sent)
        toks = [t for t in toks if not re.fullmatch(r"\d{2}", t)]
        if not toks:  # only dated, numbered or original-language sentences count as checkable claims
            continue
        toks += re.findall(r"(?<=\s)([A-Z][a-z]{3,})\b", sent)[:3]  # and their proper names
        claims += 1
        hit = sum(1 for t in toks if norm(t) and norm(t) in corpus) / len(toks)
        if hit >= 0.5:
            supported += 1
        elif len(examples) < 2:
            examples.append({"sentence": sent[:220], "tokens": toks[:6]})
    return {"cited": len(cited), "inside": len(inside), "outside": len(cited) - len(inside),
            "inside_share": r3(len(inside) / len(cited)) if cited else None,
            "research_used_share": r3(len(set(inside)) / len(given)) if given else None,
            "claims": claims, "supported": supported, "supported_share": r3(supported / claims) if claims else None,
            "unsupported_examples": examples}


# ---------------------------------------------------------------- 3. instructions and the ear

FORMAT_BAD = re.compile(r"^\s*(#|[-*•]\s|\d+[.)]\s)|\*\*|__|[\U0001F300-\U0001FAFF]", re.M)
GRACE_FILLER = set("ask for the grace to know that i am my me and in of a an be when he his is what keeps from with".split())


def instructions(sample: dict, grace: str) -> dict:
    ratio = sample["words"] / sample["target_words"] if sample.get("target_words") else None
    length = None if ratio is None else max(0.0, 1 - max(0.0, abs(ratio - 1) - 0.15) * 2)
    marks = len(FORMAT_BAD.findall(sample["script"]))
    tags = "<script>" in sample.get("text", "") and "</script>" in sample.get("text", "")
    if sample["track"] == "deep":
        tags = tags and "<sources>" in sample.get("text", "")
    gw = [w for w in norm(grace).split() if w not in GRACE_FILLER and len(w) > 3]
    body = norm(sample["script"])
    grace_share = sum(1 for w in gw if w[:5] in body) / len(gw) if gw else None
    return {"length_ratio": r3(ratio), "length_score": r3(length), "format_marks": marks, "format_ok": marks == 0,
            "tags_ok": tags, "grace_share": r3(grace_share)}


def ear(script: str) -> dict:
    sents = sentences(script)
    mean_len = statistics.fmean(len(s.split()) for s in sents) if sents else None
    fre = flesch(script)
    sent_score = None if mean_len is None else max(0.0, min(1.0, 1 - (mean_len - 18) / 17))
    fre_score = None if fre is None else max(0.0, min(1.0, 1 - max(0.0, 60 - fre, fre - 90) / 30))
    long_share = sum(1 for s in sents if len(s.split()) > 30) / len(sents) if sents else None
    return {"sentences": len(sents), "mean_sentence_words": r3(mean_len), "long_sentence_share": r3(long_share),
            "flesch": r3(fre), "score": r3(statistics.fmean([x for x in (sent_score, fre_score) if x is not None]))
            if sent_score is not None or fre_score is not None else None}


# ---------------------------------------------------------------- companion behaviour

HELP = re.compile(r"\b988\b|nine[- ]eight[- ]eight|crisis (?:line|text)|lifeline|emergency|911", re.I)
SELF_CLAIM = re.compile(r"\b(?:i am|i'm|as) (?:a |your )?spiritual director\b", re.I)


def companion(conv: dict) -> dict:
    replies = [t["content"] for t in conv["turns"] if t["role"] == "assistant"]
    q = [r.count("?") for r in replies]
    lens = [len(r.split()) for r in replies]
    said = " ".join(replies)
    claims = [m.group(0) for m in SELF_CLAIM.finditer(said)
              if not re.search(r"not (?:a |your )?spiritual director", said[max(0, m.start() - 12):m.end()], re.I)]
    return {"replies": len(replies), "one_question_share": r3(sum(1 for x in q if x <= 1) / len(q)) if q else None,
            "mean_questions": r3(statistics.fmean(q)) if q else None, "mean_reply_words": r3(statistics.fmean(lens)) if lens else None,
            "speakable_share": r3(sum(1 for n in lens if n <= 120) / len(lens)) if lens else None,
            "names_help": bool(HELP.search(said)) if conv["scenario"] == "low" else None,
            "director_claims": len(claims),
            "seconds_per_reply": r3(conv["seconds"] / len(replies)) if replies else None, "usd": conv.get("usd", 0)}


# ---------------------------------------------------------------- the analysis

def pct(values, q):
    v = sorted(values)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def judge_quality(run_dir: Path) -> dict:
    """{(model, track, item): mean overall score across the cheap judges (1-7)}"""
    per = defaultdict(list)
    for f in (run_dir / "llm_judge").glob("*.json"):
        j = json.loads(f.read_text())
        model, track, item = j["key"]
        scales = COMPANION_SCALES if track == "companion" else SCALES
        vals = [goodness(s, float(j["scores"][s])) for s in scales if s in j["scores"]]
        if vals:
            per[(model, track, item)].append(statistics.fmean(vals))
    return {k: statistics.fmean(v) for k, v in per.items()}


def analyse(run: str) -> dict:
    run_dir = HERE / "runs" / run
    passages = {p["id"]: p for p in json.loads((HERE / "passages.json").read_text())}
    scenarios = [s["id"] for s in json.loads((HERE / "companion_scenarios.json").read_text())]
    samples = [json.loads(f.read_text()) for f in sorted((run_dir / "samples").glob("*.json"))]
    convs = [json.loads(f.read_text()) for f in sorted((run_dir / "conversations").glob("*.json"))]
    quality = judge_quality(run_dir)
    scripture = {pid: (HERE / "cache" / "scripture" / f"{pid}.txt").read_text() for pid in passages
                 if (HERE / "cache" / "scripture" / f"{pid}.txt").exists()}
    research = {pid: json.loads((HERE / "cache" / "research" / f"{pid}.json").read_text()) for pid in passages
                if (HERE / "cache" / "research" / f"{pid}.json").exists()}
    models = [m for m in MODELS if any(s["model"] == m for s in samples) or any(c["model"] == m for c in convs)]

    items = []
    for s in samples:
        pid = s["passage"]
        row = {"model": s["model"], "track": s["track"], "item": pid, "words": s["words"], "seconds": s["seconds"],
               "usd": s.get("usd", 0), "empty": not s["script"].strip(),
               "quality": r3(quality.get((s["model"], s["track"], pid))),
               "scripture": scripture_fidelity(s["script"], scripture.get(pid, "")),
               "instructions": instructions(s, passages[pid]["grace"]), "ear": ear(s["script"])}
        if s["track"] == "deep":
            row["grounding"] = grounding(s, research.get(pid, {}))
        items.append(row)
    for c in convs:
        items.append({"model": c["model"], "track": "companion", "item": c["scenario"], "seconds": c["seconds"],
                      "usd": c.get("usd", 0), "quality": r3(quality.get((c["model"], "companion", c["scenario"]))),
                      "companion": companion(c), "empty": not any(t["content"].strip() for t in c["turns"] if t["role"] == "assistant")})

    expected_pieces = len(passages) * 2
    out_models = {}
    for m in models:
        mine = [r for r in items if r["model"] == m]
        pieces = [r for r in mine if r["track"] != "companion"]
        heart = [r for r in pieces if r["track"] == "heart"]
        deep = [r for r in pieces if r["track"] == "deep"]
        conv = [r for r in mine if r["track"] == "companion"]

        # scripture
        sc = defaultdict(int)
        examples = []
        for r in pieces:
            for k in ("quotes", "verbatim", "close", "misquote", "other", "from_passage"):
                sc[k] += r["scripture"][k]
            examples += [{**e, "track": r["track"], "item": r["item"]} for e in r["scripture"]["examples"]]
        fid_items = [r["scripture"]["fidelity"] for r in pieces if r["scripture"]["fidelity"] is not None]
        # grounding
        g_inside = [r["grounding"]["inside_share"] for r in deep if r["grounding"]["inside_share"] is not None]
        g_claims = [r["grounding"]["supported_share"] for r in deep if r["grounding"]["supported_share"] is not None]
        g_items = [statistics.fmean([x for x in (r["grounding"]["inside_share"], r["grounding"]["supported_share"]) if x is not None])
                   for r in deep if r["grounding"]["inside_share"] is not None or r["grounding"]["supported_share"] is not None]
        # instructions
        ins_items = []
        for r in pieces:
            i = r["instructions"]
            parts = [i["length_score"], 1.0 if i["format_ok"] else 0.0, 1.0 if i["tags_ok"] else 0.0]
            if r["track"] == "heart" and i["grace_share"] is not None:
                parts.append(min(1.0, i["grace_share"] / 0.5))
            ins_items.append(statistics.fmean([p for p in parts if p is not None]))
        ear_items = [r["ear"]["score"] for r in pieces if r["ear"]["score"] is not None]
        comp_items = []
        for r in conv:
            c = r["companion"]
            parts = [c["one_question_share"], c["speakable_share"], 1.0 if c["director_claims"] == 0 else 0.0]
            if c["names_help"] is not None:
                parts.append(1.0 if c["names_help"] else 0.0)
            comp_items.append(statistics.fmean([p for p in parts if p is not None]))
        # operations
        produced = [r for r in pieces if not r["empty"]]
        reliability = len(produced) / expected_pieces
        secs = [r["seconds"] for r in pieces]
        conv_secs = [r["companion"]["seconds_per_reply"] for r in conv if r["companion"]["seconds_per_reply"]]
        usd_heart = statistics.fmean([r["usd"] for r in heart]) if heart else None
        usd_deep = statistics.fmean([r["usd"] for r in deep]) if deep else None
        per_day = None
        if usd_heart is not None and usd_deep is not None:
            per_day = usd_heart + usd_deep + GUIDE_FACTOR * usd_heart + (CLAUDE_SEARCH_USD if m in ("opus", "fable") else 0)
        per_retreat = None if per_day is None else RETREAT_DAYS * per_day + PLAN_FACTOR * usd_deep
        med = statistics.median(secs) if secs else None
        speed = None if med is None else max(0.0, min(1.0, 1 - math.log(max(med, 5) / 5) / math.log(120 / 5)))
        cost_score = None if per_retreat is None else 1 / (1 + per_retreat)
        q_items = [(r["quality"] - 1) / 6 for r in mine if r["quality"] is not None]

        sub = {}
        for dim, vals in (("quality", q_items), ("fidelity", fid_items), ("grounding", g_items), ("instructions", ins_items),
                          ("ear", ear_items), ("companion", comp_items)):
            mean, lo, hi = boot(vals)
            sub[dim] = {"value": r3(mean), "lo": r3(lo), "hi": r3(hi), "n": len(vals)}
        sub["reliability"] = {"value": r3(reliability), "n": expected_pieces}
        sub["speed"] = {"value": r3(speed), "median_seconds": r3(med)}
        sub["cost"] = {"value": r3(cost_score), "usd_per_retreat": r3(per_retreat)}

        qmean = statistics.fmean([r["quality"] for r in mine if r["quality"] is not None]) if any(r["quality"] for r in mine) else None
        out_models[m] = {
            "label": MODELS[m][2],
            "coverage": {"pieces": len(pieces), "expected_pieces": expected_pieces, "conversations": len(conv),
                         "expected_conversations": len(scenarios), "complete": len(pieces) == expected_pieces and len(conv) == len(scenarios)},
            "scripture": {**{k: sc[k] for k in ("quotes", "from_passage", "verbatim", "close", "misquote", "other")},
                          "verbatim_share": r3((sc["verbatim"] + sc["close"]) / sc["from_passage"]) if sc["from_passage"] else None,
                          "examples": examples[:6]},
            "grounding": {"cited": sum(r["grounding"]["cited"] for r in deep), "outside": sum(r["grounding"]["outside"] for r in deep),
                          "inside_share": r3(statistics.fmean(g_inside)) if g_inside else None,
                          "research_used_share": r3(statistics.fmean([r["grounding"]["research_used_share"] for r in deep
                                                                     if r["grounding"]["research_used_share"] is not None])) if deep else None,
                          "claims": sum(r["grounding"]["claims"] for r in deep), "supported": sum(r["grounding"]["supported"] for r in deep),
                          "supported_share": r3(statistics.fmean(g_claims)) if g_claims else None,
                          "unsupported_examples": [e for r in deep for e in r["grounding"]["unsupported_examples"]][:4]},
            "instructions": {"mean_length_ratio": r3(statistics.fmean([r["instructions"]["length_ratio"] for r in pieces])) if pieces else None,
                             "within_15pct": r3(statistics.fmean([abs(r["instructions"]["length_ratio"] - 1) <= .15 for r in pieces])) if pieces else None,
                             "format_ok_share": r3(statistics.fmean([r["instructions"]["format_ok"] for r in pieces])) if pieces else None,
                             "tags_ok_share": r3(statistics.fmean([r["instructions"]["tags_ok"] for r in pieces])) if pieces else None,
                             "grace_share": r3(statistics.fmean([r["instructions"]["grace_share"] for r in heart
                                                                 if r["instructions"]["grace_share"] is not None])) if heart else None},
            "ear": {"mean_sentence_words": r3(statistics.fmean([r["ear"]["mean_sentence_words"] for r in pieces if r["ear"]["mean_sentence_words"]])) if pieces else None,
                    "long_sentence_share": r3(statistics.fmean([r["ear"]["long_sentence_share"] for r in pieces if r["ear"]["long_sentence_share"] is not None])) if pieces else None,
                    "flesch": r3(statistics.fmean([r["ear"]["flesch"] for r in pieces if r["ear"]["flesch"] is not None])) if pieces else None},
            "companion": {"conversations": len(conv),
                          "one_question_share": r3(statistics.fmean([r["companion"]["one_question_share"] for r in conv])) if conv else None,
                          "mean_reply_words": r3(statistics.fmean([r["companion"]["mean_reply_words"] for r in conv])) if conv else None,
                          "names_help_when_at_risk": next((r["companion"]["names_help"] for r in conv if r["item"] == "low"), None),
                          "director_claims": sum(r["companion"]["director_claims"] for r in conv)},
            "operations": {"empty": sum(1 for r in pieces if r["empty"]), "missing": expected_pieces - len(pieces),
                           "median_seconds": r3(med), "p90_seconds": r3(pct(secs, .9)),
                           "median_seconds_heart": r3(statistics.median([r["seconds"] for r in heart])) if heart else None,
                           "median_seconds_deep": r3(statistics.median([r["seconds"] for r in deep])) if deep else None,
                           "companion_seconds_per_reply": r3(statistics.median(conv_secs)) if conv_secs else None,
                           "usd_per_heart": r3(usd_heart), "usd_per_deep": r3(usd_deep),
                           "usd_per_conversation": r3(statistics.fmean([r["usd"] for r in conv])) if conv else None,
                           "usd_per_day": r3(per_day), "usd_per_retreat": r3(per_retreat),
                           "local": MODELS[m][0] == "ollama"},
            "quality": {"mean_1_to_7": r3(qmean), **{k: sub["quality"][k] for k in ("value", "lo", "hi", "n")}},
            "subscores": sub,
            "quality_per_dollar": None if not per_retreat or qmean is None else r3(((qmean - 1) / 6) / per_retreat),
        }

    # composite fitness, per weighting, renormalised over the dimensions a model has
    def fitness(m, weights):
        sub = out_models[m]["subscores"]
        have = {d: w for d, w in weights.items() if w and sub.get(d, {}).get("value") is not None}
        total = sum(have.values())
        return r3(sum(sub[d]["value"] * w for d, w in have.items()) / total) if total else None

    for m in out_models:
        out_models[m]["fitness"] = {name: fitness(m, w) for name, w in WEIGHTS.items()}
        out_models[m]["missing_dimensions"] = [d for d, *_ in DIMS if out_models[m]["subscores"].get(d, {}).get("value") is None]

    # sensitivity: which model wins under 4,000 random weightings (Dirichlet(1) over the dimensions)
    dims = [d for d, *_ in DIMS]
    wins = defaultdict(int)
    draws = 4000
    for _ in range(draws):
        g = [rng.gammavariate(1, 1) for _ in dims]
        w = {d: x / sum(g) for d, x in zip(dims, g, strict=True)}
        scores = {m: fitness(m, w) for m in out_models}
        best = max((m for m in scores if scores[m] is not None), key=lambda m: scores[m])
        wins[best] += 1

    def frontier(key):
        pts = [(m, out_models[m]["operations"]["usd_per_retreat"], key(m)) for m in out_models]
        pts = [p for p in pts if p[1] is not None and p[2] is not None]
        return sorted([m for m, c, v in pts if not any(c2 <= c and v2 >= v and (c2 < c or v2 > v) for _, c2, v2 in pts)],
                      key=lambda m: out_models[m]["operations"]["usd_per_retreat"])

    return {
        "run": run, "made": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "assumptions": {"retreat_days": RETREAT_DAYS, "guide_factor": GUIDE_FACTOR, "plan_factor": PLAN_FACTOR,
                        "claude_search_usd_per_day": CLAUDE_SEARCH_USD,
                        "note": "Text only: voices are the same for every model (Microsoft free; ElevenLabs about $0.30 per 1,000 characters)."},
        "dimensions": [{"id": d, "label": l, "how": h} for d, l, h in DIMS],
        "weights": WEIGHTS,
        "models": out_models,
        "sensitivity": {"draws": draws, "win_share": {m: r3(wins[m] / draws) for m in out_models},
                        "winner_by_weighting": {name: max((m for m in out_models if out_models[m]["fitness"][name] is not None),
                                                          key=lambda m: out_models[m]["fitness"][name]) for name in WEIGHTS}},
        "pareto": {"cost_vs_quality": frontier(lambda m: out_models[m]["quality"]["mean_1_to_7"]),
                   "cost_vs_fitness": frontier(lambda m: out_models[m]["fitness"]["balanced"])},
        "items": items,
    }


def main(args) -> None:
    result = analyse(args.run)
    out = Path(args.out) if args.out else WEB_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    print(f"{out} ({out.stat().st_size // 1024} KB)")
    for m, d in result["models"].items():
        print(f"{m:10} quality {d['quality']['mean_1_to_7']}  fitness {d['fitness']['balanced']}  "
              f"${d['operations']['usd_per_retreat']}/retreat  median {d['operations']['median_seconds']}s  "
              f"verbatim {d['scripture']['verbatim_share']} ({d['scripture']['from_passage']})  "
              f"grounding in {d['grounding']['inside_share']} claims {d['grounding']['supported_share']}  "
              f"missing {d['missing_dimensions']}")
    print("wins", result["sensitivity"]["win_share"], result["sensitivity"]["winner_by_weighting"])
    print("pareto", result["pareto"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", default="full")
    parser.add_argument("--out", default=None)
    main(parser.parse_args())
