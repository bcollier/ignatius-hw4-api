"""Prompts for planning a retreat and writing each day's scripts."""

import contextvars
import re

# Sent ahead of every prompt to every model (see llm._call and jetstream.complete),
# so each step understands the tradition it's writing for.
BACKGROUND = """Background for this work (for your understanding; don't recite it to the listener):

This app, Ignatius at Home, turns material a person has chosen (a retreat handout, scripture passages, readings, images) into a guided audio retreat they pray at home, usually one day at a time, often week after week over months.

The Spiritual Exercises. Ignatius of Loyola (1491 to 1556), founder of the Jesuits, wrote the Spiritual Exercises as a manual for the person who gives them, not a book to be read straight through; Pope Paul III approved them in 1548. They are a structured path of prayer, traditionally arranged in four "weeks" that are stages rather than calendar weeks: the first on God's love, sin and mercy; the second on the life of Christ and following him; the third on his passion; the fourth on the resurrection and love in action. They open with the Principle and Foundation, on what we are made for and the freedom (Ignatius calls it indifference) to choose what leads there. Features that matter for this app:
- Each prayer period begins by asking for a specific grace, "what I want and desire," named plainly.
- Imaginative contemplation: entering a Gospel scene with the senses, as if present. Application of the senses gathers a scene through each sense.
- The colloquy: speaking to God or to Christ "as one friend speaks to another," usually at the end of a period, closing with the Our Father.
- Repetition: returning to the points where one felt more consolation or desolation, rather than always moving on to new material.
- Consolation and desolation: the inner movements of the heart toward or away from God (peace, desire, tears, or dryness, restlessness). Noticing them is the heart of discernment; the director helps the person notice, not tell them what to feel.
- The Examen: a short daily review of the day with gratitude, noticing where God was present.
- The one who gives the Exercises should not push the retreatant but "let the Creator deal directly with the creature" (Annotation 15). Guidance should invite, not instruct or moralize.

A retreat. A retreat is a period set apart for prayer. The full Exercises can be made over about thirty days in silence, or, following Ignatius' nineteenth annotation, "in daily life": at home over many months, with a set time of prayer each day and regular meetings with a spiritual director. Programs of this kind often run through the school year week by week, with a handout of scripture and readings for each week. That is the listener here: an adult praying for perhaps half an hour a day, in the middle of work and family life.

Lectio divina. "Divine reading" is the monastic practice of slow, prayerful reading of scripture, central to the Rule of Saint Benedict. The Carthusian Guigo II (twelfth century) named its steps in The Ladder of Monks: lectio (reading), meditatio (meditation), oratio (prayer), contemplatio (contemplation). Pope Benedict XVI's Verbum Domini (2010, paragraph 87) describes them as: what does the text say in itself; what does it say to us; what do we say to the Lord in response; and taking up God's way of seeing, with actio (action) following.

How a day in this app is prayed. The day opens by asking for the grace, then a short silence. The passage is read four times, loosely following lectio divina: the first reading simply to hear it (lectio); then the reflection for the heart, and the second reading, listening for what the text says to me (meditatio); then the deep dive on its theology, history and interpretation, and the third reading, listening for what God may offer or ask; then a silence framed by a bell (contemplatio); then the last reading, answered in one's own words as a colloquy (oratio), and a closing. The reflection is heard between the first and second readings; the deep dive between the second and third. Everything is heard aloud, once, in order, so each part should prepare for the next and never assume the listener can look back at a page.
"""


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
SEARCH_OFF = "You cannot search the web. Make only claims you are confident are well established, and say when a point is debated."


GUIDE_TAILOR = """You write the short spoken guidance for one day of a prayer retreat: the lines a guide says before each reading and around the silence. The listener hears them in this order, between the parts shown below: opening, then the first reading, the reflection for the heart, the second reading, the deep dive, the third reading, the silence, the last reading, and the closing.

For each line you're given the text the retreat uses by default. Keep its purpose, its place and roughly its length, and adapt it to this day so the parts hold together: point back to an image, word or question from the reflection or the deep dive where it helps the listener pray the next reading (for example, the line before the second reading can recall what the reflection invited them to notice). Don't summarize the reflection or the deep dive. Keep the opening's request for the grace word for word. Each line is read aloud once, by the same calm voice.

""" + HOUSE_STYLE + """

Reply with only a JSON object whose keys are the line names given, each with its text."""


def tailor_input(context: str, heart: str, deep: str, lines: dict) -> str:
    parts = [context]
    if heart:
        parts.append(f"<heart_reflection>\n{heart}\n</heart_reflection>")
    if deep:
        parts.append(f"<deep_dive>\n{deep}\n</deep_dive>")
    parts.append("<default_lines>\n" + "\n".join(f"{name}: {text}" for name, text in lines.items()) + "\n</default_lines>")
    return "\n\n".join(parts)


def day_context(retreat_title: str, day: dict, image_description: str | None, heart: str | None = None) -> str:
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
    if heart:
        lines.append(
            "\nThe reflection for the heart, which the listener hears just before this, between the first and "
            "second readings. Build on it and don't repeat it:\n<heart_reflection>\n" + heart + "\n</heart_reflection>"
        )
    return "\n".join(lines)
