"""Write the guided exercises with Claude Opus 5.5: narration in the app's voice, and how
long each silence and journaling pause should be. Writes (or adds to) practice.json for
the web app; the narration is recorded by record_practice.py.

Run: .venv/bin/python tools/make_practice.py OUT_DIR [practice|dossier|examen|my-dossier|birth]
  practice: "Creating a Prayer Practice" (a daily practice and a weekly review)
  dossier:  "Your Life's Faith Story" (a thirty-minute guided life review)
  examen:   "The Examen" (the end-of-day review of the day with God)
  my-dossier: "Prayer Over My Dossier" (praise over the facts of one's life, about forty minutes)
  birth:    "Meditation on My Birth" (imaginative prayer at one's own birth, about twenty-five minutes)
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import llm, pricing, prompts  # noqa: E402

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

SYSTEM = prompts._prompt("practice_writer")


# The dossier exercise from the user's retreat handout (Disposition Days, Prayer Unit 2,
# Day 3), which says it's adapted from Margaret Silf's Inner Compass; summarized here,
# and the narration puts the questions in its own words.
DOSSIER = """Dossier, a summary of your life's faith story. Ask the Holy Spirit to guide a review of the significant moments of your life. Journal on the most important people and events of your life, or make a timeline marking the joys and sorrows of each decade, or draw images or symbols that connect you to your story. Ask the Lord to direct your memory and your emotions. This is just for you; you share only what you're comfortable sharing.
Remembering means piecing together again what has become fragmented or broken. Ask for the grace to remember the fragments of your life in a way that reveals the patterns leading toward your wholeness with God.
Six areas to reflect on: 1. the family you were born into and significant people from your childhood, and how they shaped who you are. 2. events and people, some before you were born, some recent, some still present, that made an important difference in your life, and how. 3. the seasons of your life, joys and losses, victories and failures, times of trial, healing and transformation; what seems most significant. 4. looking back, where you notice God's presence or absence in those seasons; periods that strengthened you or prepared you for growth; griefs and wounds that still weigh you down. 5. your personality and traits and who you understand yourself to be; how God works through your giftedness; how your struggles get in the way of God's flow in your life. 6. where you have experienced God's still, small voice; where God's powerful presence and guidance; where you long for more of God's grace and love.
End by sharing honestly with God whatever has risen to the surface: what does God want you to notice?
(Adapted in the handout from Inner Compass: An Invitation to Ignatian Spirituality, by Margaret Silf.)"""

DOSSIER_TASK = prompts._prompt("practice_dossier")

# The Examen, from the notes the user pasted (summarized).
EXAMEN = """The Ignatian Examen: a short daily prayer from the spirituality of Saint Ignatius. Not mainly a verdict on whether you were good or bad, but reviewing the day with God: where was God present, what was happening inside me, what drew me toward greater love, freedom and life, and what pulled me away. Commonly five steps, ten to fifteen minutes, unhurried.
1. Become aware of God's presence. Stop and become quiet; you are placing yourself before the God who already knows everything that happened today. A prayer like: God, you have been with me throughout this day; help me become aware of your presence now, and see this day as you see it. A few slow breaths; notice the body and the emotions present without fixing them. The review isn't done by introspection alone: begin by asking for grace, help me see.
2. Review the day with gratitude. Before failures, look for gifts, significant or ordinary (a good conversation, coffee, someone making you laugh, finishing something, a moment outside, music, food, the body doing what you needed, someone's patience, unexpected peace). Don't manufacture gratitude; if the day was awful, don't pretend. Simply ask: what was given to me today? Gratitude comes early because life is received as gift.
3. Review the day, especially the inner movements. Replay the day like scrolling through a recording: waking, breakfast, the commute, the first conversation, work, lunch, afternoon, evening, now. Don't analyze every minute; notice moments with emotional energy (irritation, unexpected peace, jealousy, anxiety, excitement, resentment, generosity, embarrassment, loneliness, compassion, impatience, hope) and stay there. Emotions and desires are information. Consolation is not just feeling good: movement toward faith, hope, love, generosity, courage, connection and freedom; something hard (an awkward apology) can be consolation. Desolation is not just feeling bad: movement toward isolation, hopelessness, self-absorption, resentment, fear, numbness; something pleasant can leave you empty. Questions: when did I feel most alive, most diminished; when was I loving, when did I close myself off; what gave real peace, what left me restless; what did I desire and where did it lead; when did I feel close to God, or far. Notice before judging.
4. Face what went wrong and ask forgiveness. Bring the particular thing before God (impatience, a lie, ignoring someone, selfishness, indulged resentment). Honesty, not self-loathing: I see what I did, I see how I hurt that person, I'm sorry, forgive me, help me understand what was happening in me. Ask: what was I actually seeking? (fear under anger, wanting respect under showing off, longing to feel valuable under envy, fear of failure under avoidance). That doesn't excuse it but reveals something guilt alone wouldn't. Something good, like unusual patience, is acknowledged as grace, not self-congratulation.
5. Look toward tomorrow. What is actually coming (a meeting, a hard conversation, class, family, a decision, a likely temptation, a worry)? Given what I saw today, what grace do I need tomorrow? Make it concrete: not "make me better" but "when I talk to that person, help me listen instead of trying to win", or "give me courage to do the thing I've been avoiding", or simply "give me patience". Entrust tomorrow to God; traditionally end with the Our Father.
Over time the middle review reveals patterns, which connects the Examen to discernment. A short variation: for what moment today am I most grateful, and least grateful? Stay with those two."""

EXAMEN_TASK = prompts._prompt("practice_examen")

# "Prayer Over My Dossier" (a retreat worksheet, Prayer Unit 2, Day 3), summarized.
MY_DOSSIER = """A prayer over the "vital statistics" of one's life. As each fact is recalled and written down, the person lifts their mind to God their Maker and praises and thanks God for the details of their history and of themselves, knowing God chose that they would exist in this place and time, with these parents, genes and traits. They ponder God's choices for them and in them; that God loved them before they existed; and that God did not finish making them once, long ago, but keeps creating them, the Spirit still hoping they will grow until they love as God loves.
Worksheet fields: father (full name, birth date, birthplace); mother (full name, birth date, birthplace); myself (full name, birth date, birthplace; gender, race, ethnicity; hair color, eye color, physical build); siblings (name and birth date for each); notes and significant family details; the places I have lived; my extended family.
Traits, six of each: traits and characteristics formed in me before I had a choice (my environment, temperament such as self-assurance or anxiety, intelligence, the languages I speak, habits of study, activities I enjoy); traits inherited from my parents or extended family, some I like and some I'd rather not have; personal qualities I particularly like (quiet or outgoing, thorough, sensitive to others' feelings, energetic, accomplishing a great deal), noted as gifts from the One who makes me, for which I praise and thank God; personal qualities I don't particularly like (height, an attitude I can't shake, an illness such as diabetes, a negative self-image), which the worksheet also asks me to acknowledge as gifts from the One who makes me. For all this, I praise and thank God.
A last page for additional reflections, sketches, images and memories."""

# "Meditation on my birth: an exercise in prayer of memory and imagination", by Dr.
# Eileen C. Burke-Sullivan (Creighton University), summarized.
BIRTH = """Uses memory of family stories, or imagination drawn from what one knows of human birth (even from film or television). Quiet yourself, feet on the floor, breathing until somewhat peaceful; ask God for the gift of memory or imagination to be an observer at your own birth.
Remember what family has told you: where your mother was when labor began, early or late or on time, a long labor or sudden. If never told, imagine how your mother looked then; if adopted and you never knew your birth parents, imagine them as you'd like them to have looked. Stand as an observer with God the Creator at your side; you may not see God but recognize the divine presence by its warmth.
Imagine the setting: doctors and nurses, or a midwife and helpers, all busy and focused on your mother; they can't see you as you are now, nor God. Hear your mother in labor, crying out or breathing hard, suddenly gasping; your father may or may not be there, but someone is cheering her on.
Then you see the child you were, tiny, defenseless, struggling to breathe in a strange new world as you are born. The doctor lifts you and prompts your first breath; the nurse washes, measures and weighs you and prepares to hand you to your mother. But for a moment God steps forward, takes you in the divine arms and gazes at you with complete love, delighted, almost playful with the pleasure of holding you. Then God hands you back, looks deep into your present eyes and tells you that you are God's beloved child, that God longed for the moment you would come into life, and longs even more for you to know how deeply you are loved.
Stay with this moment. What do you want to say to this God who loves you utterly? Do you believe what God says? Converse with God about what is in your heart. Near the end, pray the Glory Be slowly, bowing from the waist toward God.
Afterwards: What are your feelings from the prayer? Can you talk with Jesus for a few minutes about it; is it real to you; any sense of connection with the Creator? Journal what you "saw", what you thought, and your own longing from this kind of prayer; if disappointed, journal the negative feelings too. Were there distractions, when, from what, and how did you handle them?"""

TASKS = {
    "practice": lambda: f"<handout>\n{HANDOUT}\n</handout>",
    "dossier": lambda: f"{DOSSIER_TASK}\n\n<handout>\n{DOSSIER}\n</handout>",
    "examen": lambda: f"{EXAMEN_TASK}\n\n<notes>\n{EXAMEN}\n</notes>",
    "my-dossier": lambda: f"{prompts._prompt('practice_my_dossier')}\n\n<worksheet>\n{MY_DOSSIER}\n</worksheet>",
    "birth": lambda: f"{prompts._prompt('practice_birth')}\n\n<notes>\n{BIRTH}\n</notes>",
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
