"""Write the guided exercises with Claude Opus 5.5: narration in the app's voice, and how
long each silence and journaling pause should be. Writes (or adds to) practice.json for
the web app; the narration is recorded by record_practice.py.

Run: .venv/bin/python tools/make_practice.py OUT_DIR [practice|dossier]
  practice: "Creating a Prayer Practice" (a daily practice and a weekly review)
  dossier:  "Your Life's Faith Story" (a thirty-minute guided life review)
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import llm, pricing  # noqa: E402

MODEL = "anthropic/claude-opus-5.5"

# The steps of the practice, as the user's handout lays them out (summarized).
HANDOUT = """Creating a Prayer Practice
Beginning prayer: in your prayer place, intentionally place yourself in the presence of God; take a few deep breaths. As you breathe slowly, remind yourself that you are never alone and that God is with you right now.
Name the grace you seek: Saint Ignatius invites us to name the grace we seek at the start of every prayer period. After placing yourself in God's presence, go to God with the question: what is the grace I seek? Notice what word arises within you: rest, peace, clarity, courage, hope, light, love, mercy, and so on. When a word arises, turn it into a prayer: "God, as I begin, I seek the grace of ___."
(Then the person prays: with scripture or in quiet.)
Review of daily prayer: at the end of the prayer time, Saint Ignatius invites a review of prayer: a look back over the minutes spent in prayer with the question, what happened here? A spiritual journal helps: a word or phrase from scripture that caught your heart, a memory or image God stirred in prayer, the feelings that rose within you (hopeful, sad, frightened, afraid, peaceful).
Weekly review of prayer: look back over your daily notes of prayer and jot down what has happened in this week's movement of God within your heart and within your life. Looking backwards and reviewing our prayer helps us continue our forward movement and growth in God."""

SCHEMA = {
    "type": "object",
    "properties": {
        "sessions": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "segments": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["speak", "silence", "journal"]},
                        "step": {"type": "string"},
                        "text": {"type": "string"},
                        "seconds": {"type": "integer"},
                        "question": {"type": "string"},
                        "prompts": {"type": "array", "items": {"type": "string"}},
                        "why": {"type": "string"},
                    },
                    "required": ["kind", "step", "text", "seconds", "question", "prompts", "why"],
                    "additionalProperties": False,
                }},
            },
            "required": ["id", "title", "summary", "segments"],
            "additionalProperties": False,
        }},
    },
    "required": ["sessions"],
    "additionalProperties": False,
}

SYSTEM = """You write the narration for a guided prayer practice in Ignatius at Home, an app for praying Ignatian retreats at home. A calm voice leads one person through a prayer practice, out loud, with real silences and pauses to write in a journal. The person may be praying in the early morning or late at night, alone, with their phone.

Write two sessions from the handout's steps (the person's own parish handout, below): "daily", the daily practice (beginning prayer, naming the grace, a time of prayer, the review of prayer with journaling, and a brief close), and "weekly", the weekly review of prayer (settling, looking back over the week's daily notes, which the app shows on screen during the pause, journaling what happened in the week's movement of God, and a brief close). Put the handout's ideas in your own words; keep the Ignatian substance (the grace, the review, consolation and desolation in plain words) and don't quote the handout at length.

Each session is a list of segments, in the order they happen:
- "speak": narration the voice reads (text). Short, warm, unhurried sentences written for the ear: no lists, no headings, no parentheses, no abbreviations ("Saint", not "St."), numbers in words. One idea per segment; a segment is usually two to six sentences. Say "Saint Ignatius", not just "Ignatius", the first time.
- "silence": a timed silence with nothing to do but pray or breathe (seconds, and a short label in "question", e.g. "Breathe, and rest in God's presence").
- "journal": a timed pause to write (seconds), with the one question shown on screen ("question") and two to four short prompts that help someone start writing ("prompts"). The narration just before it should ask the same question aloud and say how long they have.
For every segment give "step" (which part of the practice it belongs to: Beginning prayer, Name the grace, Prayer, Review of prayer, Weekly review, Close) and "why" (one sentence on the timing or wording choice, for the app's designers; not shown). Unused fields are empty strings, 0 or [].

Decide the timing yourself, for real people: long enough to settle and write something honest, short enough that a busy person can do the whole daily practice most days. Think about how long it takes to take a few slow breaths, to let a word arise, to pray with scripture or in quiet, and to write two or three sentences by hand or on a phone. The daily practice should come to roughly fifteen to twenty minutes in all, including a prayer time in the middle; the weekly review roughly ten to fifteen. Journaling pauses are usually two to three minutes; the naming-the-grace pause about one to two minutes; the prayer time itself about eight to ten minutes of silence, introduced so the person knows they can pray with a passage (for example today's retreat reading) or simply stay in quiet, and closed gently.

End each session by speaking a brief close (the daily one can close with "God, as I begin…" only if it fits; usually a short thanksgiving). Don't mention the app, buttons, screens or timers except to say that the question will be on the screen and a soft sound will end the pause."""


# The dossier exercise from the user's retreat handout (Disposition Days, Prayer Unit 2,
# Day 3), which says it's adapted from Margaret Silf's Inner Compass; summarized here,
# and the narration puts the questions in its own words.
DOSSIER = """Dossier, a summary of your life's faith story. Ask the Holy Spirit to guide a review of the significant moments of your life. Journal on the most important people and events of your life, or make a timeline marking the joys and sorrows of each decade, or draw images or symbols that connect you to your story. Ask the Lord to direct your memory and your emotions. This is just for you; you share only what you're comfortable sharing.
Remembering means piecing together again what has become fragmented or broken. Ask for the grace to remember the fragments of your life in a way that reveals the patterns leading toward your wholeness with God.
Six areas to reflect on: 1. the family you were born into and significant people from your childhood, and how they shaped who you are. 2. events and people, some before you were born, some recent, some still present, that made an important difference in your life, and how. 3. the seasons of your life, joys and losses, victories and failures, times of trial, healing and transformation; what seems most significant. 4. looking back, where you notice God's presence or absence in those seasons; periods that strengthened you or prepared you for growth; griefs and wounds that still weigh you down. 5. your personality and traits and who you understand yourself to be; how God works through your giftedness; how your struggles get in the way of God's flow in your life. 6. where you have experienced God's still, small voice; where God's powerful presence and guidance; where you long for more of God's grace and love.
End by sharing honestly with God whatever has risen to the surface: what does God want you to notice?
(Adapted in the handout from Inner Compass: An Invitation to Ignatian Spirituality, by Margaret Silf.)"""

DOSSIER_TASK = """Write one session, id "dossier", titled for the person (for example "Your Life's Faith Story"), from the handout below: a guided life review in which the voice leads the person through the six areas one at a time while they write. It must come to thirty minutes in all, as close as you can: add up every segment (speaking at about one hundred and forty words a minute, plus every silence and journaling pause) and make the total thirty minutes. Begin by settling and asking the Holy Spirit to guide memory and emotions, and for the grace named in the handout; say plainly that this is just for them and they can write, list, sketch a timeline or draw. Then give each of the six areas its own short spoken introduction (put the questions in your own words, gently, one or two at a time; never read them as a list) followed by a journal pause with the question on screen and helpful prompts. Size each pause by how much there is to remember and how heavy it is: the seasons of life and where God was present or absent need the most time; a hard area (wounds, griefs) needs a gentle word before and after, and permission to write only what they are comfortable with. End with time to speak honestly with God about what rose to the surface, asking what God wants them to notice, and a brief close. In the summary, credit the source: "Adapted from a retreat handout based on Margaret Silf's Inner Compass." Reply with {"sessions": [that one session]}."""

TASKS = {
    "practice": lambda: f"<handout>\n{HANDOUT}\n</handout>",
    "dossier": lambda: f"{DOSSIER_TASK}\n\n<handout>\n{DOSSIER}\n</handout>",
}


async def main(out: Path, which: str = "practice") -> None:
    meter = pricing.Meter(MODEL, await pricing.prices())
    message = await llm._call(meter, system=SYSTEM, max_tokens=16000,
                              messages=[{"role": "user", "content": TASKS[which]()}],
                              output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
    made = llm._parse_json(llm._text(message))
    out.mkdir(parents=True, exist_ok=True)
    path = out / "practice.json"
    practice = json.loads(path.read_text()) if path.exists() else {"sessions": []}
    new_ids = {s["id"] for s in made["sessions"]}
    practice["sessions"] = [s for s in practice["sessions"] if s["id"] not in new_ids] + made["sessions"]
    practice["model"] = MODEL
    practice.setdefault("script_usd", {})[which] = round(meter.usd, 4)
    path.write_text(json.dumps(practice, indent=1, ensure_ascii=False))
    for s in made["sessions"]:
        speak = sum(len(g["text"]) for g in s["segments"] if g["kind"] == "speak")
        quiet = sum(g["seconds"] for g in s["segments"] if g["kind"] != "speak")
        words = sum(len(g["text"].split()) for g in s["segments"] if g["kind"] == "speak")
        total = quiet + words / 140 * 60
        print(f"{s['id']}: {len(s['segments'])} segments, {speak:,} characters spoken, {quiet // 60}:{quiet % 60:02d} "
              f"of silence and journaling, about {total / 60:.1f} minutes in all")
    print(f"script cost ${meter.usd:.3f}")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "practice"))
