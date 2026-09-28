"""Talk it over, taking turns: the companion without a live voice model.

The browser listens (its own speech recognition, free), sends each thing the person
says here, and plays the reply, recorded sentence by sentence in a free Microsoft
voice. The "brain" that writes the replies is chosen: the free Jetstream model, or,
for premium accounts, a Claude model through OpenRouter or an OpenAI model. A brain
costs only for the words it reads and writes, so a conversation that is mostly the
person talking costs a fraction of a live voice model's per-minute price.

Same instructions, context and memory as the live companion (talk.context); the
conversation is saved through talk.end like any other.
"""

import logging
import tempfile
import time
from pathlib import Path

import httpx

from . import config, jetstream, llm, llm_log, pricing, prompts, tts

log = logging.getLogger(__name__)

PROVIDER = "turns"
DEFAULT_VOICE = "en-US-AndrewMultilingualNeural"
MAX_TURNS = 300  # per session (a long conversation is summarized as it goes, below)
SUMMARY_OVER = 12_000  # characters of verbatim conversation before the older part is summarized
KEEP_TURNS = 12  # the most recent turns always stay word for word
SUMMARY = prompts._prompt("companion_summary")
# How the conversation is happening right now, so the companion writes for it: talking
# (transcribed speech in, spoken reply out), typing with the reply spoken aloud, or text.
MODES = {"voice": "companion_spoken", "listen": "companion_typed_heard", "text": "companion_text"}
MODE_NOTES = {
    "voice": "[They've switched to talking: their words now reach you transcribed from speech, and your replies are spoken aloud.]",
    "listen": "[They've switched to typing, and your replies are now spoken aloud to them; they may be staying quiet where they are.]",
    "text": "[They've switched to text: they type, and read your replies on the screen; nothing is spoken.]",
}
MAX_SAY = 2_000  # characters in one thing the person says
MAX_SPEAK = 700  # characters in one sentence sent to be spoken
REPLY_TOKENS = 700
THINKING_ROOM = 4  # reasoning models (Muse Glimmer, GPT-5.x) spend tokens thinking before they answer
# Rough costs are for a reply with the usual context (the companion's instructions, the
# person's notes and the retreat), after the first reply for Claude, whose context is cached.
OPENAI_BRAINS = {"gpt-5.5": "OpenAI GPT-5.5 (about 3 seconds, a few cents a reply)",
                 "gpt-5.4-mini": "OpenAI GPT-5.4 mini (about 2 seconds, a fraction of a cent)"}
CLAUDE_BRAINS = {
    "anthropic/claude-fable-5.1": "Claude Fable 5.1 (the finest writer; about 6 seconds, 2 to 3 cents a reply)",
    "anthropic/claude-opus-5.5": "Claude Opus 5.5 (about 6 seconds, 1 to 2 cents a reply)",
    "anthropic/claude-haiku-4.5": "Claude Haiku 4.5 (about 2 seconds, under a cent)",
}

# Added to the companion's instructions: it is heard, not read.
SPOKEN = prompts._prompt("companion_spoken")


def brains(full: bool) -> dict[str, str]:
    """The brains this person may choose, by id. The first is the default."""
    out = {}
    free = pricing.jetstream_models()
    if free:
        out["free"] = f"{free[0][1].removesuffix(' (free)')} (free; slower, 10 to 20 seconds a reply)"
    if full:
        if config.LLM_MODE == "openrouter":
            out.update(CLAUDE_BRAINS)
        if config.OPENAI_API_KEY:
            out.update({f"openai:{m}": label for m, label in OPENAI_BRAINS.items()})
    return out


def provider_info() -> dict:
    return {"label": "Free voice (takes turns)", "voices": tts.FREE_VOICES, "default_voice": DEFAULT_VOICE, "turns": True}


def check_brain(brain: str, full: bool) -> str:
    allowed = brains(full)
    if not allowed:
        raise ValueError("No companion model is set up on this server.")
    return brain if brain in allowed else next(iter(allowed))


async def reply(session: dict, text: str | None, mode: str = "voice") -> str:
    """The companion's next words. `text` is what the person just said (None to begin, or
    to welcome them back when the conversation continues); `mode` is how they're talking
    now, and a change of mode is noted in the conversation where it happened."""
    mode = mode if mode in MODES else "voice"
    if session.get("mode") and session["mode"] != mode:
        note = ("Note", MODE_NOTES[mode])
        session["turns"].append(note)
        session["log"].append(note)
    session["mode"] = mode
    if text is not None:
        said_now = ("Them", text.strip()[:MAX_SAY])
        session["turns"].append(said_now)
        session["log"].append(said_now)
    if len(session["log"]) >= MAX_TURNS:
        return "We've talked a long while. Let's stop here for today; you can start a new conversation any time."
    await _keep_it_short(session)
    said = "\n".join(words if who == "Note" else f"{who}: {words}" for who, words in session["turns"])
    earlier = (f"Earlier in this conversation (a summary of the part no longer shown word for word):\n"
               f"{session['summary']}\n\n" if session.get("summary") else "")
    if text is None and said:
        ask = (f"{earlier}The conversation so far:\n{said}\n\nThe person has come back to continue this conversation "
               "(perhaps after a pause, perhaps now typing instead of talking, or the other way round). Welcome them back "
               "in a sentence, naturally, without recapping, and invite them to go on. Reply with only what you say.")
    elif said:
        ask = f"{earlier}The conversation so far:\n{said}\n\nReply with only what you say next."
    else:
        ask = "The person has just started the conversation. Greet them and ask your first question. Reply with only what you say."
    system = session["instructions"] + "\n\n" + _how_we_talk(session, mode)
    llm_log.tag(user_id=session["user_id"], email=session["email"], retreat_id=session.get("log_retreat_id"), purpose="talk_turn")
    words, usd = await _think(session["brain"], system, ask)
    words = words.strip().strip('"')
    session["turns"].append(("You", words))
    session["log"].append(("You", words))
    session["usd"] = round(session.get("usd", 0) + usd, 4)
    return words


def _how_we_talk(session: dict, mode: str) -> str:
    """The guidance for the current mode: the person's own version (Agents page) or the app's."""
    name = MODES[mode]
    return (session.get("custom") or {}).get(name) or (session.get("spoken") if name == "companion_spoken" else None) \
        or (SPOKEN if name == "companion_spoken" else prompts._prompt(name))


async def _keep_it_short(session: dict) -> None:
    """Once the verbatim conversation passes SUMMARY_OVER characters, fold all but the last
    KEEP_TURNS turns into the running summary, so a long conversation (spoken, typed, or
    both, over several sittings) never outgrows the model's context. The full record is
    kept in session["log"] and saved as it is."""
    if sum(len(w) for _, w in session["turns"]) <= SUMMARY_OVER or len(session["turns"]) <= KEEP_TURNS:
        return
    older, recent = session["turns"][:-KEEP_TURNS], session["turns"][-KEEP_TURNS:]
    text = ("Summary so far:\n" + (session.get("summary") or "(none)") + "\n\nOlder part of the conversation:\n"
            + "\n".join(w if who == "Note" else f"{'Person' if who == 'Them' else 'Companion'}: {w}" for who, w in older))
    llm_log.tag(user_id=session["user_id"], email=session["email"], retreat_id=session.get("log_retreat_id"),
                purpose="talk_summary")
    try:
        summary, usd = await _summarize(session, text)
    except Exception:  # a summary that fails leaves the conversation as it was; the next turn tries again
        log.warning("couldn't summarize a long conversation", exc_info=True)
        return
    session["summary"] = summary.strip()[:4000]
    session["turns"] = recent
    session["usd"] = round(session.get("usd", 0) + usd, 4)


async def _summarize(session: dict, text: str) -> tuple[str, float]:
    """With the conversation's own brain when it can (Claude or the free model); an OpenAI
    brain's summary is written by the default model for the account."""
    brain = session["brain"]
    instructions = session.get("summary_prompt") or SUMMARY
    if brain == "free" or (brain.startswith("openai:") and not session["full"]):
        model = pricing.jetstream_models()[0][0]
    elif brain.startswith("openai:"):
        model = config.LLM_MODEL
    else:
        model = brain
    meter = pricing.Meter(model, await pricing.prices())
    return await llm.condense_text(instructions, text, meter), meter.usd


async def _think(brain: str, system: str, ask: str) -> tuple[str, float]:
    if brain == "free":
        model = pricing.jetstream_models()[0][0]
        meter = pricing.Meter(model, await pricing.prices())
        try:
            return await jetstream.complete(pricing.api_model(model), system, ask, meter, max_tokens=REPLY_TOKENS * THINKING_ROOM), 0.0
        except jetstream.JetstreamError as exc:
            raise llm.LLMError(str(exc)) from exc
    if brain.startswith("openai:"):
        return await _openai(brain.removeprefix("openai:"), system, ask)
    meter = pricing.Meter(brain, await pricing.prices())
    # The instructions and context are the same on every turn: cached, later turns read them at a tenth of the price.
    cached = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    message = await llm._call(meter, system=cached, max_tokens=REPLY_TOKENS, messages=[{"role": "user", "content": ask}])
    return llm._text(message), meter.usd


async def _openai(model: str, system: str, ask: str) -> tuple[str, float]:
    """One OpenAI chat completion, logged like every other model call."""
    timer = llm_log.Timer()
    body = {"model": model, "max_completion_tokens": REPLY_TOKENS * THINKING_ROOM,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": ask}]}
    error, text, usage = None, "", {}
    try:
        async with httpx.AsyncClient(timeout=90) as http:
            r = await http.post("https://api.openai.com/v1/chat/completions", json=body,
                                headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"})
        if r.status_code != 200:
            error = f"OpenAI answered {r.status_code}: {r.text[:300]}"
            raise llm.LLMError("The OpenAI model didn't answer. Try again, or choose another brain.")
        data = r.json()
        text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage") or {}
    finally:
        usd = await _openai_usd(model, usage)
        await llm_log.record(provider="openai", model=model, system=system, messages=body["messages"][1:],
                             response_text=text or None, response_extra=None,
                             usage={"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0),
                                    "usd": usd}, duration_ms=timer.ms, error=error)
    return text, usd


async def _openai_usd(model: str, usage: dict) -> float:
    """From OpenRouter's price list, which carries OpenAI's prices; 0 when unknown."""
    price = (await pricing.prices()).get(f"openai/{model}") or {}
    return round(usage.get("prompt_tokens", 0) * price.get("prompt", 0) + usage.get("completion_tokens", 0) * price.get("completion", 0), 5)


async def speak(voice: str, text: str) -> bytes:
    """One sentence in a free Microsoft voice, as MP3."""
    voice = voice if voice in tts.FREE_VOICES else DEFAULT_VOICE
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "say.mp3"
        await tts.synthesize(text[:MAX_SPEAK], voice, out)
        return out.read_bytes()


def new_session(user, retreat: dict | None, instructions: str, voice: str, brain: str) -> dict:
    return {"user_id": user.id, "email": user.log_email, "provider": PROVIDER, "voice": voice, "brain": brain,
            "started": time.time(), "max": config.TALK_MAX_SECONDS,
            "retreat_id": retreat["id"] if retreat else None,
            "log_retreat_id": retreat["id"] if retreat and retreat.get("user_id") == user.id and not retreat.get("read_only") else None,
            "retreat_title": (retreat.get("plan") or {}).get("title") if retreat else None,
            "instructions": instructions, "full": user.full, "turns": [], "log": [], "summary": "", "usd": 0.0}
