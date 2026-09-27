"""Retreats in a series: earlier retreats become context for planning and writing a
new one, so a nine-month retreat taken week by week keeps its thread.

Everything from the earlier retreats goes in, in order: each day's title, source,
grace, passage, reflection and deep dive. When that exceeds the size budget, the
oldest weeks are shortened first (within a week: deep dives, then reflections,
then passages), so recent weeks stay complete, while every day's title, source and
grace always stays.
"""

from . import config, prompts

MAX_PREVIOUS = 60  # retreats in one series

INSTRUCTIONS = prompts._prompt("series")


def _entry(retreat: dict) -> dict:
    """One earlier retreat as a list of days with the fields that can be dropped."""
    plan = retreat.get("plan") or {}
    days = []
    for d in plan.get("days", []):
        state = retreat.get("days", {}).get(str(d["day"]), {})
        tracks = state.get("tracks", {})
        days.append({
            "head": f"Day {d['day']}: {d['title']} ({d.get('source_ref', '')})\nGrace: {d.get('grace', '')}",
            "passage": d.get("passage_text", ""),
            "heart": (tracks.get("heart") or {}).get("script", ""),
            "deep": (tracks.get("deep") or {}).get("script", ""),
        })
    return {"title": plan.get("title") or retreat.get("filename", "Retreat"), "summary": plan.get("summary", ""), "days": days}


def _render(entries: list[dict]) -> str:
    parts = ["<series>"]
    for n, e in enumerate(entries, start=1):
        parts.append(f"\n=== Week {n} of {len(entries)}: {e['title']} ===\n{e['summary']}")
        for day in e["days"]:
            parts.append("\n" + day["head"])
            if day["passage"]:
                parts.append(f"Passage: {day['passage']}")
            if day["heart"]:
                parts.append(f"Reflection for the heart: {day['heart']}")
            if day["deep"]:
                parts.append(f"Deep dive: {day['deep']}")
    parts.append("</series>")
    return "\n".join(parts)


LABELS = {"passage": "Passage: ", "heart": "Reflection for the heart: ", "deep": "Deep dive: "}


def context(previous: list[dict], budget: int) -> tuple[str, dict]:
    """The series as text within `budget` characters, oldest material shortened first.
    Returns (text, stats). Sizes are tracked as fields are dropped, so this stays fast
    for a long series."""
    entries = [_entry(r) for r in previous if r.get("plan")]
    if not entries:
        return "", {"retreats": 0, "characters": 0, "shortened": 0}
    size = len(_render(entries))
    shortened = 0
    for e in entries:  # oldest week first, so the most recent weeks stay complete
        for field in ("deep", "heart", "passage"):  # within a week: deep dives, then reflections, then passages
            for day in e["days"]:
                if size <= budget:
                    break
                if day[field]:
                    size -= len(LABELS[field]) + len(day[field]) + 1  # the line and its newline
                    day[field] = ""
                    shortened += 1
    text = _render(entries)
    while len(entries) > 1 and len(text) > budget:  # only headers left and still too long: keep recent weeks
        entries.pop(0)
        text = _render(entries)
    return text, {"retreats": len(entries), "characters": len(text), "shortened": shortened}


def budget_for(model: str) -> int:
    from .pricing import is_jetstream

    return config.SERIES_MAX_CHARS_FREE if is_jetstream(model) else config.SERIES_MAX_CHARS
