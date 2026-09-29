"""A handout's front matter: what it says before its first day (a week's introduction,
the graces to pray for, how to pray this week). Copied word for word from the source,
never written by a model, and shown before Day 1 as "Before you begin".

Lines a handout repeats as a page header or footer ("PREPARATION DAYS / PRAYER UNIT 3")
are left out, and the printed line breaks are joined back into paragraphs."""

import re

from . import llm

MIN_WORDS = 30  # fewer words before Day 1 are a title page, not an introduction (the graces still count)
MAX_CHARS = 6000
FIRST_DAY = re.compile(r"^[^\n]{0,60}?\bDAY\s*(?:1|ONE)\b", re.I | re.M)
GRACES = re.compile(r"^\s*(?:I|we)\s+(?:pray|ask)\s+for\s+the\s+(?:following\s+)?graces?\s*:?\s*(.+)$", re.I)
PAGE = re.compile(r"\[Page \d+\]")


def _key(line: str) -> str:
    return re.sub(r"\W+", " ", line).strip().lower()


def extract(text: str, plan: dict) -> dict | None:
    """{"text": the introduction in paragraphs (or ""), "graces": the week's graces (or "")}, or None."""
    if not text or plan.get("mode") != "follows_source" or not plan.get("days"):
        return None
    cuts = [m.start() for m in [FIRST_DAY.search(text)] if m]
    first = plan["days"][0].get("passage_text", "")
    at = llm.passage_start(text, first) if first else None
    if at is not None:
        cuts.append(at)
    if not cuts:
        return None
    front = text[:min(cuts)]
    # Header and footer lines: short lines that also appear elsewhere in the handout.
    counts: dict[str, int] = {}
    for line in PAGE.sub("", text).splitlines():
        k = _key(line)
        if k and len(k) < 80:
            counts[k] = counts.get(k, 0) + 1
    paragraphs, current, seen, graces, in_graces = [], [], set(), "", False
    for raw in PAGE.sub("\n", front).splitlines():
        line = raw.strip()
        k = _key(line)
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            in_graces = False
            continue
        if counts.get(k, 0) > 1 or k in seen:
            continue
        seen.add(k)
        g = GRACES.match(line)
        if g or in_graces:  # the graces, to the end of their paragraph
            graces = f"{graces} {g.group(1) if g else line}".strip()
            in_graces = True
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in paragraphs if not _heading(p)]
    body = "\n\n".join(p for p in paragraphs if p)
    if len(body.split()) < MIN_WORDS:
        body = ""
    if not body and not graces:
        return None
    return {"text": body[:MAX_CHARS], "graces": graces}


def _heading(paragraph: str) -> bool:
    """The handout's own title lines: short and mostly in capitals."""
    letters = [c for c in paragraph if c.isalpha()]
    return len(paragraph.split()) < 15 and bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.4
