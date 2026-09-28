"""Every agent in the app, for the Agents page: what it does, when it runs, what it's
given, its default prompt (app/agent_prompts/), and the person's own version and model.

A person's version is saved to their account (profile.save_agent) and used wherever that
agent runs: New retreat fills its prompts from it (and sends them with the request), and
the server applies the rest in each job (prompts.custom, set by profile.use_agents).
Blocks the app always adds (the background, the house style, the format rules) are shown
read only: the code parses what they ask for, or every agent shares them.
"""

from . import config, inspiration, my_examen, pricing, profile, prompts, talk, talk_turns

MAX_PROMPT = prompts.MAX_PROMPT_CHARS

# Model choices. "plan", "write" and "talk" are the menus the web page already keeps (New
# retreat's planning and writing models, the Talk page's voice and brain); "server" means
# the model is chosen here and saved with the prompt.
AGENTS = [
    {"id": "plan", "group": "Making a retreat", "name": "The planner", "file": "plan.md", "model": "plan",
     "what": "Reads your document and plans the retreat: how many days, in what order, the passage, grace and focus for each, and which pictures go with which day.",
     "when": "Once, after you upload material (or after the idea step).",
     "gets": "The document's text and images, your About me notes, and any earlier retreats in the series.",
     "default": lambda: prompts.PLAN_INSTRUCTIONS, "fixed": "plan_rules"},
    {"id": "preview", "group": "Making a retreat", "name": "The first look", "file": "preview.md", "model": None,
     "what": "As soon as you choose a file, reads its start and gives it a working title and two or three sentences on what it is, so you can check it's the right file.",
     "when": "When you choose a file on New retreat, before anything is made. It uses the fast model of the family you chose for planning (Llama 4 Scout or Claude Haiku 4.5), so the answer comes in a second or two.",
     "gets": "The file name and the start of the document (or its first page, if it's a scan or photo).",
     "default": lambda: prompts._prompt("preview")},
    {"id": "inspiration", "group": "Making a retreat", "name": "Idea to passages", "file": "inspiration.md", "model": "plan",
     "what": "For a retreat started from an idea or a photo: chooses one scripture passage a day (the text itself is fetched from the World English Bible, never written by the model).",
     "when": "Before planning, for \"Start from an idea or a photo\".",
     "gets": "Your idea, the number of days, and the photo.",
     "default": lambda: inspiration.SYSTEM},
    {"id": "heart_companion", "group": "Making a retreat", "name": "For the heart: a companion's voice", "file": "heart_companion.md", "model": "write",
     "what": "Writes each day's short spoken reflection for the heart, in the voice of a warm prayer companion. Includes the house style.",
     "when": "Each day, first of the three parts.",
     "gets": "The day's passage, grace, focus and painting, and what the earlier days said.",
     "default": lambda: prompts.HEART_PRESETS["companion"], "fixed": "heart_format"},
    {"id": "heart_christ", "group": "Making a retreat", "name": "For the heart: Jesus speaking to you", "file": "heart_christ.md", "model": "write",
     "what": "The reflection for the heart in the voice of Jesus, as in Ignatian imaginative prayer, when you choose it. Includes the house style.",
     "when": "Each day, instead of the companion's voice, if you choose it.",
     "gets": "The same as the companion's voice.",
     "default": lambda: prompts.HEART_PRESETS["christ"], "fixed": "heart_format"},
    {"id": "deep_dive", "group": "Making a retreat", "name": "The deep dive (the book study)", "file": "deep_dive.md", "model": "write",
     "what": "Writes the close reading for the mind: setting, original-language words, how the Church has read the passage, real open questions. Includes the house style.",
     "when": "Each day, after the heart reflection.",
     "gets": "The day, the heart reflection, the web research, and what earlier days said.",
     "default": lambda: prompts.DEEP_INSTRUCTIONS, "fixed": "deep_format"},
    {"id": "guide_tailor", "group": "Making a retreat", "name": "The spoken guidance", "file": "guide_tailor.md", "model": "write",
     "what": "Tailors the short guiding lines (asking for the grace, before each reading, the silence, the close) to the day. The grace is kept word for word.",
     "when": "Each day, last, if tailoring is on.",
     "gets": "The default lines, the heart reflection and the deep dive.",
     "default": lambda: prompts.GUIDE_TAILOR},
    {"id": "companion", "group": "Talking it over", "name": "The companion", "file": "companion.md", "model": "talk",
     "what": "The prayer companion you talk with (Talk now, Talk it over): it listens the way spiritual directors are trained to, mostly with questions.",
     "when": "Every conversation, spoken live or taking turns.",
     "gets": "Your notes and wishes, the time where you are, when you last talked, its memory of earlier talks, and your retreat day by day.",
     "default": lambda: talk.COMPANION},
    {"id": "companion_spoken", "group": "Talking it over", "name": "Taking turns: how it speaks", "file": "companion_spoken.md", "model": None,
     "what": "Added to the companion when it takes turns (the free voice or a chosen brain): speak briefly, one question at a time, no lists.",
     "when": "Every reply in taking-turns mode.",
     "gets": "The conversation so far.",
     "default": lambda: talk_turns.SPOKEN},
    {"id": "companion_typed_heard", "group": "Talking it over", "name": "Typing, reply spoken: how it speaks", "file": "companion_typed_heard.md", "model": None,
     "what": "Added to the companion when the person types and hears the replies aloud (so they can stay quiet).",
     "when": "Every reply in that mode.",
     "gets": "The conversation so far.",
     "default": lambda: prompts._prompt("companion_typed_heard")},
    {"id": "companion_text", "group": "Talking it over", "name": "Text chat: how it writes", "file": "companion_text.md", "model": None,
     "what": "Added to the companion when the conversation is written: the person types and reads.",
     "when": "Every reply in text chat.",
     "gets": "The conversation so far.",
     "default": lambda: prompts._prompt("companion_text")},
    {"id": "companion_summary", "group": "Talking it over", "name": "The running summary", "file": "companion_summary.md", "model": None,
     "what": "Keeps a long conversation within reach: once it gets long, the older part is folded into a short summary and the latest exchanges stay word for word.",
     "when": "During a long conversation that takes turns (spoken or typed).",
     "gets": "The summary so far and the older part of the conversation.",
     "default": lambda: talk_turns.SUMMARY},
    {"id": "companion_memory", "group": "Talking it over", "name": "The companion's memory", "file": "companion_memory.md", "model": "server",
     "what": "Folds older conversations into a short memory the companion keeps. Keep {limit} where the length goes.",
     "when": "After a conversation, once older transcripts get long.",
     "gets": "The existing memory and the older transcripts.",
     "default": lambda: talk.REMEMBER, "needs": ["{limit}"]},
    {"id": "my_examen", "group": "Practices", "name": "Your own Examen", "file": "my_examen.md", "model": "server", "full_only": True,
     "what": "Writes an end-of-day Examen around your life, with silences and journaling pauses, to be recorded for you.",
     "when": "When you press \"Make my Examen\".",
     "gets": "Your About me notes and what you add about your days.",
     "default": lambda: my_examen.SYSTEM},
]

# Always added, shown read only.
FIXED = [
    ("background", "Background (every agent)", "Sent first to every agent: the Spiritual Exercises, retreats, lectio divina and how a day is prayed here.", lambda: prompts.BACKGROUND),
    ("house_style", "House style (every writer)", "Part of the heart, deep dive and guidance prompts: writing for the ear and for prayer.", lambda: prompts.HOUSE_STYLE),
    ("about_the_person", "How your About me notes are used", "Put before your notes in every call made for you.", lambda: prompts._prompt("about_the_person")),
    ("plan_rules", "Planner: format rules", "Added after the planner's prompt; the app reads the plan as structured data.", lambda: prompts.PLAN_FIXED),
    ("heart_format", "Heart: format rules", "Added after the heart prompt: the length and the tags the app reads.", lambda: prompts.HEART_FIXED),
    ("deep_format", "Deep dive: format rules", "Added after the deep dive prompt: the length, the tags and the sources list.", lambda: prompts.DEEP_FIXED),
    ("research", "Deep dive: research notes", "How the deep dive is told to use the web research (and the prompt that writes the searches).", lambda: prompts._prompt("research")),
    ("retreat_so_far", "The retreat so far", "Before what earlier days said, from day 2 on.", lambda: prompts.RETREAT_SO_FAR_NOTE),
]


def _server_models(agent: dict, user) -> list[dict]:
    if agent["id"] == "my_examen":  # written with Claude's structured output
        return [{"id": m, "label": label} for m, _, label in pricing.MODELS]
    free = [{"id": m, "label": label} for m, label in pricing.jetstream_models()]
    return ([{"id": m, "label": label} for m, _, label in pricing.MODELS] if user.full else []) + free


def _default_model(agent: dict, user) -> str:
    if agent["id"] == "my_examen" or user.full:
        return config.LLM_MODEL
    free = pricing.jetstream_models()
    return free[0][0] if free else ""


async def listing(user) -> dict:
    mine = await profile.agent_settings(user.id)
    out = []
    for a in AGENTS:
        if a.get("full_only") and not user.full:
            continue
        item = {k: a.get(k) for k in ("id", "group", "name", "file", "what", "when", "gets", "needs")}
        item.update(default=a["default"](), custom=mine["prompts"].get(a["id"], ""), model_kind=a["model"],
                    fixed=a.get("fixed"))
        if a["model"] == "server":
            item["models"] = _server_models(a, user)
            item["model"] = mine["models"].get(a["id"]) or _default_model(a, user)
        out.append(item)
    fixed = [{"id": i, "name": n, "what": w, "text": t()} for i, n, w, t in FIXED]
    return {"agents": out, "fixed": fixed, "max_chars": MAX_PROMPT}


def check(agent_id: str, prompt: str | None, model: str | None, user) -> dict:
    """The agent, if the change is allowed; raises ValueError with a message otherwise."""
    agent = next((a for a in AGENTS if a["id"] == agent_id), None)
    if not agent or (agent.get("full_only") and not user.full):
        raise ValueError("There's no such agent.")
    if prompt and len(prompt) > MAX_PROMPT:
        raise ValueError(f"That prompt is too long (the limit is {MAX_PROMPT:,} characters).")
    for needed in agent.get("needs") or []:
        if prompt and needed not in prompt:
            raise ValueError(f"Keep {needed} in this prompt: the app puts a value there.")
    if model and (agent["model"] != "server" or model not in {m["id"] for m in _server_models(agent, user)}):
        raise ValueError("That model isn't available for this agent.")
    return agent
