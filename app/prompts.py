"""Prompts for planning a retreat and writing each day's scripts."""

import contextvars
import re
from pathlib import Path

# Sent ahead of every prompt to every model (see llm._call and jetstream.complete),
# so each step understands the tradition it's writing for.
# The prompts themselves live in prompt_texts/ as plain text, so they're easy to read
# and edit (and to see whole in the app's Advanced settings). They were written with
# Claude Fable from a brief describing the whole app; see docs/prompt-design/.
PROMPT_DIR = Path(__file__).parent / "prompt_texts"


def _prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text().strip()


def _tagged(tag: str, text: str) -> str:
    return f"<{tag}>\n{text}\n</{tag}>"


# Prepended to every model call (planning, writing, research queries, the companion).
BACKGROUND = _tagged("background", _prompt("background"))


# What the person has written about themselves ("user info.md"), set for the length
# of a job so every model call made for them includes it (llm._call, jetstream.complete).
PERSON: contextvars.ContextVar[str] = contextvars.ContextVar("person", default="")


def person_block(about: str) -> str:
    if not about.strip():
        return ""
    return (
        "About the person praying, in their own words (from their saved notes; it may be a summary). Let it shape "
        "your choice of examples, images and tone, and what you notice or ask about. Don't quote it back, don't "
        "mention that you have notes about them, and don't assume more than it says.\n<about_the_person>\n"
        + about.strip() + "\n</about_the_person>"
    )


# Appended to every script writer's instructions: writing for the ear and for prayer.
HOUSE_STYLE = _tagged("house_style", _prompt("house_style"))

# Each prompt has an editable part (shown on the web page, which can replace it)
# and a fixed part the server always adds, so the output stays parseable.

PLAN_INSTRUCTIONS = _prompt("plan")

PLAN_FIXED = """Plan at most {max_days} days.

For each day, passage_text is the text the listener will hear read aloud. Copy it word for word from the source, including the translation's wording. Remove only page numbers, running headers and footers, line-break hyphens, and verse numbers. If a day has no scripture (a consideration, a review day), use the source's own words for that day. Scanned pages are included as images; transcribe from them exactly.

grace is the grace to ask for that day, in one sentence, taken from the source when it names one. focus is one or two sentences telling the writers what the day is about. image_indexes lists every supplied image that belongs with the day, best first (the listener sees them while praying), or is empty; image_index is the first of them, or -1 for none. An image may serve several days. In images, describe each supplied image briefly for the writers; this text is never shown to the listener."""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "mode": {"type": "string", "enum": ["follows_source", "composed"]},
        "images": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "description": {"type": "string"}},
                "required": ["index", "description"],
                "additionalProperties": False,
            },
        },
        "days": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "day": {"type": "integer"},
                    "title": {"type": "string"},
                    "source_ref": {"type": "string"},
                    "passage_text": {"type": "string"},
                    # "exercise": the day is an activity (a worksheet, a review day), not a text to pray with
                    "kind": {"type": "string", "enum": ["reading", "exercise"]},
                    "grace": {"type": "string"},
                    "focus": {"type": "string"},
                    "image_index": {"type": "integer"},
                    "image_indexes": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["day", "title", "source_ref", "passage_text", "grace", "focus", "image_index", "image_indexes"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "summary", "mode", "images", "days"],
    "additionalProperties": False,
}

HEART_PRESETS = {
    # A warm spiritual companion speaking to the listener (the default).
    "companion": _prompt("heart_companion") + "\n\n" + HOUSE_STYLE,
    # In the voice of Jesus, as in Ignatian imaginative prayer and the colloquy.
    "christ": _prompt("heart_christ") + "\n\n" + HOUSE_STYLE,
}

HEART_FIXED = """Length: about {words} words. Reply with only the script inside <script></script> tags."""

DEEP_INSTRUCTIONS = _prompt("deep_dive") + "\n\n" + HOUSE_STYLE

DEEP_FIXED = """{search_note}

Length: about {words} words. Reply with the script inside <script></script> tags, then list the sources you relied on inside <sources></sources> tags, one per line with a URL when you have one. The sources are shown on screen, not read aloud."""

MAX_PROMPT_CHARS = 60000  # the defaults are long; edited prompts may be too


# Spoken guidance around the readings, in the order the lectio sequence uses it.
# Placeholders: {day}, {title}, {grace}. These are read aloud as written (no model).
GUIDE_DEFAULTS = {
    "opening": (
        "Day {day}. {title}. Settle yourself, and become aware that God is present with you now. "
        "{grace} Stay with that desire for a few moments."
    ),
    "first": (
        "We will hear today's reading four times. On this first reading, simply listen. "
        "Notice any word or phrase that catches your attention."
    ),
    "second": (
        "Now the reading a second time. Listen for how these words touch your own life. "
        "Notice what stirs in you: a memory, a desire, consolation or desolation."
    ),
    "third": "The third reading. Listen for what God may be offering you, or asking of you, in these words.",
    "silence": (
        "Now rest in silence with the word or phrase that stayed with you. "
        "Let it pray in you. A bell will mark the end of the silence."
    ),
    "last": (
        "The last reading. Let the words rest in you, then speak to God in your own words, "
        "as one friend speaks to another."
    ),
    "closing": "Thank God for this time of prayer, and close with the Our Father. Amen.",
}
GUIDE_LABELS = {
    "opening": "Opening: asking for the grace",
    "first": "Before the first reading",
    "second": "Before the second reading",
    "third": "Before the third reading",
    "silence": "Before the silence",
    "last": "Before the last reading",
    "closing": "Closing",
}
MAX_GUIDE_CHARS = 1000


def grace_request(grace: str) -> str:
    """The day's grace as a sentence to say aloud, whether or not it already
    starts with "Ask for"."""
    grace = grace.strip().rstrip(".")
    if not grace:
        return ""
    if grace.lower().startswith(("ask ", "i ask", "to ask")):
        return grace + "."
    return f"Ask for this grace: {grace[0].lower() + grace[1:]}."


def guide_text(template: str, day: dict) -> str:
    text = template
    title = re.sub(r"^\s*day\s+\d+\s*([:.\-]\s*|$)", "", day["title"], flags=re.I)  # "Day 1: Isaiah 43" -> "Isaiah 43"
    if not title.strip():
        text = text.replace("{title}.", "").replace("{title}", "")  # the title was only "Day N"
    for key, value in {"{day}": str(day["day"]), "{title}": title, "{grace}": grace_request(day.get("grace", ""))}.items():
        text = text.replace(key, value)
    return " ".join(text.split())


def defaults() -> dict:
    return {
        "plan": PLAN_INSTRUCTIONS,
        "heart": HEART_PRESETS,
        "deep": DEEP_INSTRUCTIONS,
        "guide": GUIDE_DEFAULTS,
        "guide_labels": GUIDE_LABELS,
        "companion": _prompt("companion"),  # Talk it over; editable per person on the Talk page
        "background": BACKGROUND,  # read-only in the app: what every call starts with
        "house_style": HOUSE_STYLE,
    }


SEARCH_ON = "Use web search to check specific claims (dates, word meanings, quotations, attributions) before you make them. Prefer scholarly and church sources."
SEARCH_RESULTS = (
    "Web search results for this passage are included below, numbered. Check specific claims (dates, word meanings, "
    "quotations, attributions) against them. In <sources>, list only URLs that appear in the results and that you relied on. "
    "If the results don't support a claim, leave the claim out or say it is uncertain."
)
SEARCH_QUERIES = (
    "Write three web search queries that would help a writer check facts for a short talk on the theology, history "
    "and interpretation of the passage below: its historical setting, key words in the original language, and how the "
    "church has read it. Reply with only the three queries, one per line, no numbering."
)
SEARCH_BOTH = (
    "Web search results from several search services are included below, numbered, as a head start. They vary in "
    "quality. Use your own web search as fully as the talk deserves: to go deeper, to check specific claims (dates, "
    "word meanings, quotations, attributions), and to find better sources (scholarly commentaries, church documents, "
    "the Fathers) wherever the results are thin, off topic or unreliable. Don't let the results limit you. In "
    "<sources>, list the URLs you relied on, from the results or your own searches."
)
SEARCH_OFF = "You cannot search the web. Make only claims you are confident are well established, and say when a point is debated."


GUIDE_TAILOR = _prompt("guide_tailor") + "\n\n" + HOUSE_STYLE


def tailor_input(context: str, heart: str, deep: str, lines: dict) -> str:
    parts = [context]
    if heart:
        parts.append(f"<heart_reflection>\n{heart}\n</heart_reflection>")
    if deep:
        parts.append(f"<deep_dive>\n{deep}\n</deep_dive>")
    parts.append("<default_lines>\n" + "\n".join(f"{name}: {text}" for name, text in lines.items()) + "\n</default_lines>")
    return "\n\n".join(parts)


RETREAT_SO_FAR_NOTE = (
    "This day is one day of a larger retreat, prayed one day at a time. Inside <retreat_so_far> is what the "
    "listener has already heard on earlier days (most recent first). Don't explain again what was explained "
    "there. Build on it, and where it helps, connect to it briefly (\"yesterday we heard...\"). Inside "
    "<coming_days> are the readings for the days still ahead, readings only: you may point lightly toward one "
    "when it truly connects, but don't preview or explain them. Today's passage stays the center."
)
SO_FAR_CHARS = 24000


def retreat_so_far(plan: dict, days: dict, day_no: int, max_chars: int = SO_FAR_CHARS) -> str:
    """Earlier days' reflections and deep dives (newest first, whole while they fit,
    then titles only) and the coming days' readings, for the heart and deep writers."""
    plan_days = sorted(plan.get("days", []), key=lambda d: d["day"])
    total = len(plan_days)
    earlier, budget = [], max_chars
    for d in reversed([d for d in plan_days if d["day"] < day_no]):
        tracks = (days.get(str(d["day"])) or {}).get("tracks", {})
        head = f"Day {d['day']}: {d['title']} ({d.get('source_ref', '')})"
        parts = [f"{label}:\n{tracks[k]['script']}" for k, label in (("heart", "For the heart"), ("deep", "Deep dive"))
                 if tracks.get(k, {}).get("script")]
        body = "\n\n".join(parts)
        if body and len(body) <= budget:
            earlier.append(f"{head}\n{body}")
            budget -= len(body)
        else:
            earlier.append(head + (" (not yet written)" if not body else ""))
    coming = [f"Day {d['day']}: {d['title']} ({d.get('source_ref', '')})\n{d.get('passage_text', '')}"
              for d in plan_days if d["day"] > day_no]
    if not earlier and not coming:
        return ""
    out = [RETREAT_SO_FAR_NOTE, f"Today is Day {day_no} of {total}."]
    if earlier:
        out.append("<retreat_so_far>\n" + "\n\n---\n\n".join(earlier) + "\n</retreat_so_far>")
    if coming:
        out.append("<coming_days>\n" + "\n\n".join(coming) + "\n</coming_days>")
    return "\n\n".join(out)


def day_context(retreat_title: str, day: dict, image_description: str | None, heart: str | None = None,
                so_far: str = "") -> str:
    """The user message for the heart and deep prompts. The image description helps the
    writers connect the day's picture to the text; the listener sees the image itself."""
    lines = [
        f"Retreat: {retreat_title}",
        f"Day {day['day']}: {day['title']}",
        f"Source reference: {day['source_ref']}",
        f"Grace: {day['grace']}",
        f"Focus: {day['focus']}",
    ]
    if image_description:
        # For reference only: the listener sees the painting, so the writers don't describe it.
        lines.append(f"Painting on the listener's screen (they can see it; don't describe it): {image_description}")
    if so_far:
        lines.append("\n" + so_far + "\n")
    lines.append(f"\nToday's passage:\n{day['passage_text']}")
    if heart:
        lines.append(
            "\nThe reflection for the heart, which the listener hears just before this, between the first and "
            "second readings. Build on it and don't repeat it:\n<heart_reflection>\n" + heart + "\n</heart_reflection>"
        )
    return "\n".join(lines)
