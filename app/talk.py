"""Talk it over: a live spoken conversation with a prayer companion about the retreat.

The companion is modeled on the way spiritual directors are trained to accompany
someone (listen, ask, help them notice where God is at work, give little advice),
but the app never calls it spiritual direction and it says it's an AI if asked.

Providers (each on when its key is set):
  openai  OpenAI GPT-Live (gpt-live-1) over WebRTC. The browser sends its SDP offer
          here; this server creates the session with the key and returns the answer,
          so the key never reaches the browser. A server-side hangup enforces the
          time limit.
  xai     xAI Grok voice (see _xai_session).

Free users get FREE_TALK_SECONDS a day; premium users up to TALK_MAX_SECONDS a call.
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import date

import httpx

from . import config, llm_log, prompts
from .storage import StorageError, store

log = logging.getLogger(__name__)

OPENAI_VOICES = {
    "marin": "Marin", "cedar": "Cedar", "vesper": "Vesper", "willow": "Willow", "quartz": "Quartz",
    "meridian": "Meridian", "stone": "Stone", "gleam": "Gleam", "beacon": "Beacon", "delta": "Delta",
    "cinder": "Cinder", "ripple": "Ripple",
}
XAI_VOICES: dict[str, str] = {}  # filled in by _xai_voices() below

COMPANION = """You are a prayer companion in Ignatius at Home, an app for praying a retreat at home in the Ignatian tradition. You are talking out loud with someone who is making a retreat. You are an AI, not a priest, spiritual director, counselor or therapist; if they ask, say so simply.

Accompany them the way good spiritual directors are taught to:
- Listen far more than you speak. Keep each turn short, usually one to three sentences, and often a single question. Leave room; silence is fine.
- Ask open questions about their experience of prayer: What happened when you prayed with that passage? What stayed with you? What was that like? Where did you feel drawn, or resistant?
- Help them notice movements of the heart, consolation and desolation, and where God may be at work, in their prayer and in their days. Reflect back their own words and images, and gently ask them to say more.
- Probe gently, with curiosity, not to analyze them: When you say it felt heavy, what was heavy? Is there a word or image from the reading that goes with that?
- Give very little advice. Don't tell them what God is saying, what they should feel, or what to decide. Don't moralize or teach unless they ask; if they ask about the passage, answer in a sentence or two and return to their experience.
- Help them engage with the retreat: connect what they say to the days they've prayed, the grace they asked for, a word from the passage; if they've missed days, don't scold; ask what's been happening and whether they'd like to return to a day.
- It can be good to end by asking whether there's something they want to bring into their next prayer, or to invite a short colloquy, speaking to God in their own words.
- Begin with a short, warm greeting and one open question about their prayer or this week of the retreat.

Care and limits: if they speak of wanting to harm themselves or others, being in danger, or a crisis, stop exploring, respond with care, and encourage them to call or text 988 in the US or their local emergency number, and to reach out to someone they trust now. For medical, legal, or mental-health questions, encourage them to talk with a qualified person. Encourage them to bring what matters to their own spiritual director, pastor or community if they have one. Never claim to be human.

Speak naturally, warmly and unhurriedly, in plain spoken English, without lists or headings."""


class TalkError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def providers() -> dict[str, dict]:
    out = {}
    if config.OPENAI_API_KEY:
        out["openai"] = {"label": "OpenAI (GPT-Live)", "voices": OPENAI_VOICES, "default_voice": "marin"}
    if config.XAI_API_KEY:
        out["xai"] = {"label": "xAI (Grok voice)", "voices": XAI_VOICES, "default_voice": config.XAI_DEFAULT_VOICE}
    return out


def options() -> dict:
    p = providers()
    return {
        "enabled": bool(p),
        "providers": p,
        "default_provider": next(iter(p), None),
        "free_seconds": config.FREE_TALK_SECONDS,
        "max_seconds": config.TALK_MAX_SECONDS,
    }


# ---------------------------------------------------------------- context


def context(retreat: dict | None, about: str, notes: str) -> str:
    """What the companion knows: the person's own notes, what they want from this
    conversation, and the retreat, with which days they've listened to."""
    parts = [COMPANION, prompts.BACKGROUND]
    if about.strip():
        parts.append(prompts.person_block(about))
    if notes.strip():
        parts.append("What they've said they want from this conversation companion:\n<wants>\n" + notes.strip() + "\n</wants>")
    if retreat and retreat.get("plan"):
        plan = retreat["plan"]
        lines = [f"The retreat they're making: {plan['title']}. {plan.get('summary', '')}"]
        if retreat.get("start_date"):
            lines.append(f"They started it on {retreat['start_date']}; today is {date.today().isoformat()}.")
        for d in plan["days"]:
            st = retreat["days"].get(str(d["day"]), {})
            listening = st.get("listening") or {}
            if st.get("prayed_at"):
                done = f"prayed on {st['prayed_at'][:10]}"
            elif listening.get("parts_played"):
                done = f"started, stopped at {listening.get('last_part') or 'part way'}"
            else:
                done = "not listened to yet"
            lines.append(f"\nDay {d['day']}: {d['title']} ({d.get('source_ref', '')}). Grace: {d.get('grace', '')}. Status: {done}.")
            lines.append(f"Passage: {d.get('passage_text', '')[:900]}")
            heart = (st.get("tracks", {}).get("heart") or {}).get("script", "")
            if heart:
                lines.append(f"The reflection they heard began: {heart[:500]}")
            journal = st.get("journal") or {}
            if journal.get("word"):
                lines.append(f"The word that stayed with them: {journal['word']}")
            if journal.get("note"):
                lines.append(f"Their note: {journal['note'][:400]}")
        parts.append("\n".join(lines))
    text = "\n\n".join(parts)
    return text[:48_000]  # well under the Live model's 16k-token instruction limit


# ---------------------------------------------------------------- daily allowance


def _usage_path(user_id: str) -> str:
    return f"{user_id}/talk_usage.json"


async def seconds_used_today(user_id: str) -> int:
    try:
        data = json.loads(await store.get_file(_usage_path(user_id)))
    except (StorageError, ValueError):
        return 0
    return int(data.get(date.today().isoformat(), 0))


async def add_usage(user_id: str, seconds: int) -> None:
    try:
        data = json.loads(await store.get_file(_usage_path(user_id)))
    except (StorageError, ValueError):
        data = {}
    today = date.today().isoformat()
    data = {today: int(data.get(today, 0)) + max(0, int(seconds))}  # keep only today
    await store.put_file(_usage_path(user_id), json.dumps(data).encode(), "application/json")


# Sessions started here, so /end can log them and the limit can be enforced.
_sessions: dict[str, dict] = {}


async def start(user, retreat: dict | None, about: str, notes: str, provider: str, voice: str, sdp: str | None) -> dict:
    available = providers()
    if provider not in available:
        raise TalkError(400, "That conversation service isn't set up on this server.")
    if voice not in available[provider]["voices"]:
        voice = available[provider]["default_voice"]
    if user.full:
        max_seconds = config.TALK_MAX_SECONDS
    else:
        left = config.FREE_TALK_SECONDS - await seconds_used_today(user.id)
        if left <= 5:
            raise TalkError(403, f"You've used today's {config.FREE_TALK_SECONDS} seconds of free conversation. Come back tomorrow.")
        max_seconds = left
    instructions = context(retreat, about, notes)
    if provider == "openai":
        if not sdp:
            raise TalkError(400, "Missing the browser's connection offer.")
        result = await _openai_session(instructions, voice, sdp)
    else:
        result = await _xai_session(instructions, voice)
    sid = result["session_id"]
    _sessions[sid] = {"user_id": user.id, "email": user.email or ("guest" if user.anonymous else None),
                      "provider": provider, "voice": voice, "started": time.time(), "max": max_seconds,
                      "retreat_id": retreat["id"] if retreat else None, "instructions": instructions, "full": user.full}
    if provider == "openai":
        asyncio.create_task(_hang_up_later(sid, max_seconds + 5))
    return {**result, "provider": provider, "voice": voice, "max_seconds": max_seconds}


async def end(user, session_id: str, seconds: int, transcript: str) -> None:
    s = _sessions.pop(session_id, None)
    if not s or s["user_id"] != user.id:
        return
    seconds = int(min(max(0, seconds), time.time() - s["started"] + 5))
    if not s["full"]:
        await add_usage(user.id, seconds)
    llm_log.tag(user_id=s["user_id"], email=s["email"], retreat_id=s["retreat_id"], purpose="talk")
    await llm_log.record(
        provider=s["provider"], model="gpt-live-1" if s["provider"] == "openai" else config.XAI_VOICE_MODEL,
        system=s["instructions"], messages=[], response_text=transcript[:200_000] or None,
        response_extra={"voice": s["voice"], "seconds": seconds},
        usage={"usd": round(seconds / 60 * (0.05 if s["provider"] == "openai" else config.XAI_USD_PER_MINUTE), 4)},
        duration_ms=seconds * 1000,
    )


# ---------------------------------------------------------------- OpenAI GPT-Live


async def _openai_session(instructions: str, voice: str, sdp: str) -> dict:
    from openai import AsyncOpenAI, APIError

    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    try:
        created = await client.live.create(
            session={"model": "gpt-live-1", "instructions": instructions, "audio": {"output": {"voice": voice}}},
            transport={"type": "webrtc", "sdp": sdp},
        )
    except APIError as exc:
        log.warning("gpt-live session failed: %s", exc)
        raise TalkError(502, "The conversation service couldn't start a session. Try again in a moment.") from exc
    return {"session_id": created.session.id, "sdp": created.transport.sdp}


async def _hang_up_later(session_id: str, seconds: int) -> None:
    await asyncio.sleep(seconds)
    if session_id not in _sessions:
        return
    from openai import AsyncOpenAI

    try:
        await AsyncOpenAI(api_key=config.OPENAI_API_KEY).live.sessions.hangup(session_id)
        log.info("hung up talk session %s at its limit", session_id)
    except Exception:
        pass


# ---------------------------------------------------------------- xAI Grok voice


async def _xai_session(instructions: str, voice: str) -> dict:
    raise TalkError(501, "Grok voice isn't wired up yet.")


def _new_id() -> str:
    return "local_" + uuid.uuid4().hex
