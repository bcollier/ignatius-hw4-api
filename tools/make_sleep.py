"""The sleep prayers: 10, 30 and 45 minutes of scripture about wonder and goodness, read
slowly, with long stretches of chant between, growing longer, and the last quarter music
alone as it fades. The verses are the World English Bible (public domain), fetched
word for word from bible-api.com; the few words around them are the app's own. Adds (or
replaces) the three sessions in practice.json; record_practice.py then records them.

Run: .venv/bin/python tools/make_sleep.py PRACTICE_DIR
"""

import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

BIBLE_API = "https://bible-api.com/{ref}?translation=web"
WORDS_PER_SECOND = 1.9  # a slow, sleepy reading

# (spoken lead-in, reference): lead-ins say where the words come from, nothing more.
VERSES = {
    "ps4": ("From Psalm four.", "Psalm 4:8"),
    "phil4": ("From the letter to the Philippians.", "Philippians 4:8"),
    "ps121": ("From Psalm one hundred and twenty-one.", "Psalm 121:3-4"),
    "ps19": ("From Psalm nineteen.", "Psalm 19:1-2"),
    "ps8": ("From Psalm eight.", "Psalm 8:3-4"),
    "matt6": ("From the Gospel of Matthew.", "Matthew 6:28-29"),
    "lam3": ("From the book of Lamentations.", "Lamentations 3:22-23"),
    "ps131": ("From Psalm one hundred and thirty-one.", "Psalm 131:2"),
    "ps63": ("From Psalm sixty-three.", "Psalm 63:6-7"),
    "gen1": ("From the book of Genesis.", "Genesis 1:31"),
    "isa40": ("From the prophet Isaiah.", "Isaiah 40:26"),
    "ps104": ("From Psalm one hundred and four.", "Psalm 104:24"),
    "song2": ("From the Song of Songs.", "Song of Solomon 2:11-12"),
    "ps27": ("From Psalm twenty-seven.", "Psalm 27:4"),
    "zeph3": ("From the prophet Zephaniah.", "Zephaniah 3:17"),
    "ps139": ("From Psalm one hundred and thirty-nine.", "Psalm 139:14"),
    "ps23": ("From Psalm twenty-three.", "Psalm 23:1-3"),
    "ps46": ("From Psalm forty-six.", "Psalm 46:10"),
    "num6": ("", "Numbers 6:24-26"),
}

INTRO_SHORT = ("Good night. This is a short prayer to carry you toward sleep. There is nothing left to do now, "
               "and nothing left to finish. Let the day be over. Let your body grow heavy, and let the music hold you. "
               "Between the words there will be long quiet. If you fall asleep before the end, that is exactly right.")
INTRO_LONG = ("Good night. Let the day be over now. Whatever was done today, and whatever was left undone, can rest "
              "until morning. Settle into the bed, and let it hold your weight. Breathe slowly, and let each breath "
              "out be a little longer than the one before. The God who made the stars keeps watch tonight, and does "
              "not sleep, so you may. You will hear a few words of scripture, beautiful and good to think on, with "
              "long stretches of music between. There is nothing to answer and nothing to remember. If you fall "
              "asleep before the end, that is exactly right.")
BLESSING_LEAD = "Receive the blessing given to God's people from the beginning."
GOODNIGHT = "Sleep now, in peace."

SESSIONS = [
    {"id": "sleep-10", "minutes": 10, "title": "Sleep with Prayer · 10 minutes", "intro": INTRO_SHORT,
     "verses": ["ps4", "phil4", "ps121"],
     "summary": "A few verses about God's care, read slowly, with chant between: then the music carries you to sleep."},
    {"id": "sleep-30", "minutes": 30, "title": "Sleep with Prayer · 30 minutes", "intro": INTRO_LONG,
     "verses": ["ps4", "ps19", "ps8", "matt6", "phil4", "lam3", "ps131", "ps63"],
     "summary": "Scripture about wonder and goodness, with long stretches of chant that grow longer as you drift off."},
    {"id": "sleep-45", "minutes": 45, "title": "Sleep with Prayer · 45 minutes", "intro": INTRO_LONG,
     "verses": ["ps4", "gen1", "ps19", "ps8", "isa40", "ps104", "matt6", "song2", "ps27", "zeph3", "ps139", "ps23", "ps46", "ps63"],
     "summary": "A long, slow night prayer: the goodness of creation and of God, read gently, with chant and quiet between."},
]


async def fetch(http: httpx.AsyncClient, ref: str) -> str:
    for attempt in range(4):
        r = await http.get(BIBLE_API.format(ref=ref.replace(" ", "+")))
        if r.status_code == 429:
            await asyncio.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        verses = r.json()["verses"]
        text = re.sub(r"\s+", " ", " ".join(v["text"] for v in verses)).strip()
        return text.strip("‘’“”\"'")  # the source's own quotation marks around a whole saying
    raise RuntimeError(f"bible-api kept refusing {ref}")


def spoken(text: str) -> float:
    return len(text.split()) / WORDS_PER_SECOND + 1.5


def build(session: dict, texts: dict) -> dict:
    """Speech, then quiet that grows longer; the last quarter is music alone."""
    speak = [("Settling", session["intro"])]
    for key in session["verses"]:
        lead, ref = VERSES[key]
        speak.append(("Scripture", f"{lead} {texts[key]}".strip()))
    lead, ref = VERSES["num6"]
    speak.append(("Blessing", f"{BLESSING_LEAD} {texts['num6']} {GOODNIGHT}"))
    total = session["minutes"] * 60
    tail = round(total * 0.25)
    talking = sum(spoken(t) for _, t in speak)
    gaps = len(speak) - 1
    room = max(gaps * 30, total - tail - talking)
    weights = [1 + 1.5 * i / max(1, gaps - 1) for i in range(gaps)]  # each quiet a little longer than the last
    rests = [round(room * w / sum(weights)) for w in weights]
    segments = []
    for i, (step, text) in enumerate(speak):
        segments.append({"kind": "speak", "step": step, "text": text, "seconds": 0, "question": "", "prompts": [], "why": ""})
        if i < gaps:
            segments.append({"kind": "rest", "step": "Rest", "text": "", "seconds": rests[i], "question": "", "prompts": [], "why": ""})
    segments.append({"kind": "rest", "step": "Rest", "text": "", "seconds": tail, "question": "", "prompts": [], "why": "",
                     "fade": True})  # the music fades out over this last stretch
    return {"id": session["id"], "title": session["title"], "summary": session["summary"], "group": "sleep",
            "sleep": True, "music": "chant", "segments": segments,
            "credits": "Scripture: World English Bible (public domain). Chant: see the music credits below."}


async def main(folder: Path) -> None:
    path = folder / "practice.json"
    practice = json.loads(path.read_text())
    keys = {k for s in SESSIONS for k in s["verses"]} | {"num6"}
    texts = {}
    async with httpx.AsyncClient(timeout=30) as http:
        for key in sorted(keys):
            texts[key] = await fetch(http, VERSES[key][1])
            await asyncio.sleep(1.5)  # bible-api asks for patience
    built = [build(s, texts) for s in SESSIONS]
    ids = {b["id"] for b in built}
    practice["sessions"] = [s for s in practice["sessions"] if s["id"] not in ids] + built
    path.write_text(json.dumps(practice, ensure_ascii=False, indent=1))
    for b in built:
        speech = sum(spoken(s["text"]) for s in b["segments"] if s["kind"] == "speak")
        rest = sum(s["seconds"] for s in b["segments"] if s["kind"] == "rest")
        chars = sum(len(s["text"]) for s in b["segments"] if s["kind"] == "speak")
        print(f"{b['id']}: ~{(speech + rest) / 60:.1f} min ({speech / 60:.1f} spoken), {chars} characters")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
