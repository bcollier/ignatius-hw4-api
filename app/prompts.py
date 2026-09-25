"""Prompts for planning a retreat and writing each day's scripts."""

HOUSE_STYLE = """House style for anything that will be read aloud:
- Plain prose paragraphs. No headings, lists, bold, emoji or markdown.
- No em dashes or en dashes; use commas, periods, colons or semicolons.
- No parentheses. No verse references with digits and colons; say "the Gospel of Luke, chapter fifteen" instead.
- Avoid these words and habits: delve, testament to, navigate (as a metaphor), landscape (meaning a field), realm, underscore, highlight (as a verb), elevate, honestly, "not just X but Y", "it's not about X, it's about Y", "Here's the thing", reflexive lists of three, rhetorical questions as openers, compliments to the passage before engaging it, and closing paragraphs that summarize or gesture at broader significance.
- Never invent a Hebrew or Greek word, a textual variant, a quotation, a date or a historical fact. If a point is uncertain, say it is uncertain or leave it out."""

# Each prompt has an editable part (shown on the web page, which can replace it)
# and a fixed part the server always adds, so the output stays parseable.

PLAN_INSTRUCTIONS = """You design short retreats in the Ignatian tradition from source material a user uploads: prayer handouts, scripture passages, readings and images. The user owns or has rights to the material. Work only from what they supplied.

Decide which case applies:
- follows_source: the material already lays out days (for example "Day 1", "Day 2", or a week of numbered exercises). Keep its days, order, titles and passages exactly.
- composed: the material is loose (a few verses, a reading, some images). Build a retreat of about seven days with a sensible arc, one passage or excerpt per day, drawn only from the source. Reuse a passage on a later day for repetition if the source is thin, as Ignatius recommends."""

PLAN_FIXED = """Plan at most {max_days} days.

For each day, passage_text is the text the listener will hear read aloud. Copy it word for word from the source, including the translation's wording. Remove only page numbers, running headers and footers, line-break hyphens, and verse numbers. If a day has no scripture (a consideration, a review day), use the source's own words for that day. Scanned pages are included as images; transcribe from them exactly.

grace is the grace to ask for that day, in one sentence, taken from the source when it names one. focus is one or two sentences telling the writers what the day is about. image_index is the index of the supplied image that best fits the day, or -1 for none. In images, describe each supplied image briefly for the writers; this text is never shown to the listener."""

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
                    "grace": {"type": "string"},
                    "focus": {"type": "string"},
                    "image_index": {"type": "integer"},
                },
                "required": ["day", "title", "source_ref", "passage_text", "grace", "focus", "image_index"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "summary", "mode", "images", "days"],
    "additionalProperties": False,
}

HEART_PRESETS = {
    "companion": """You write the heart-focused reflection for one day of a prayer retreat. It is heard right after the day's passage is read aloud, so it should sound like one person speaking to one other person.

Write as a warm, unhurried spiritual companion speaking to the listener as "you".

Stay with the passage and the day's grace. Notice one or two concrete words or images in the text and stay with them. Invite the listener to notice what stirs in them, consolation or desolation, without telling them what to feel. End with a simple invitation to rest with one word or phrase from the passage. No theology lecture; that belongs to the deep dive.

""" + HOUSE_STYLE,
    "christ": """You write the heart-focused reflection for one day of a prayer retreat. It is heard right after the day's passage is read aloud, so it should sound like one person speaking to one other person.

Write in the voice of Jesus speaking directly to the listener as "you", the way Ignatian imaginative prayer invites. Stay close to how Jesus speaks in the Gospels: plain, personal, never grandiose. Do not put new doctrinal claims in his mouth.

Stay with the passage and the day's grace. Notice one or two concrete words or images in the text and stay with them. Invite the listener to notice what stirs in them without telling them what to feel. End with a simple invitation to rest with one word or phrase from the passage. No theology lecture; that belongs to the deep dive.

""" + HOUSE_STYLE,
}

HEART_FIXED = """Length: about {words} words. Reply with only the script inside <script></script> tags."""

DEEP_INSTRUCTIONS = """You write the deep dive for one day of a prayer retreat: the theology, history and hermeneutics of the day's passage, for a thoughtful adult listener. It is read aloud after the heart reflection.

Cover what helps someone pray this text better: its setting in its book and history, what key words meant in the original language when that is well documented, how the church has read it (the Fathers, Ignatius, major commentators), and any real interpretive question. If the day has no scripture, treat its source text the same way: who wrote it, where it comes from, what its key terms meant.

""" + HOUSE_STYLE

DEEP_FIXED = """{search_note}

Length: about {words} words. Reply with the script inside <script></script> tags, then list the sources you relied on inside <sources></sources> tags, one per line with a URL when you have one. The sources are shown on screen, not read aloud."""

MAX_PROMPT_CHARS = 8000


def defaults() -> dict:
    return {"plan": PLAN_INSTRUCTIONS, "heart": HEART_PRESETS, "deep": DEEP_INSTRUCTIONS}


SEARCH_ON = "Use web search to check specific claims (dates, word meanings, quotations, attributions) before you make them. Prefer scholarly and church sources."
SEARCH_OFF = "You cannot search the web. Make only claims you are confident are well established, and say when a point is debated."


def day_context(retreat_title: str, day: dict, image_description: str | None) -> str:
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
        lines.append(f"Image for this day: {image_description}")
    lines.append(f"\nPassage:\n{day['passage_text']}")
    return "\n".join(lines)
