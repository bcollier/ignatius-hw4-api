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
from datetime import date, datetime, timezone

import httpx

from . import config, llm_log, prompts
from .storage import StorageError, store

log = logging.getLogger(__name__)

OPENAI_VOICES = {
    "marin": "Marin", "cedar": "Cedar", "vesper": "Vesper", "willow": "Willow", "quartz": "Quartz",
    "meridian": "Meridian", "stone": "Stone", "gleam": "Gleam", "beacon": "Beacon", "delta": "Delta",
    "cinder": "Cinder", "ripple": "Ripple",
}
XAI_VOICES: dict[str, str] = {"eve": "Eve", "ara": "Ara", "rex": "Rex", "sal": "Sal", "leo": "Leo"}  # refreshed by xai_voices()

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


def _when(local_time: str | None) -> tuple[datetime, str]:
    """The person's local time (sent by the browser) and a plain description of it."""
    try:
        now = datetime.fromisoformat(local_time) if local_time else datetime.now(timezone.utc)
    except ValueError:
        now = datetime.now(timezone.utc)
    h = now.hour
    part = ("late at night" if h < 5 else "early in the morning" if h < 8 else "in the morning" if h < 12
            else "in the afternoon" if h < 17 else "in the evening" if h < 21 else "at night")
    return now, f"It is {now.strftime('%A, %B %-d, %Y')}, {now.strftime('%-I:%M %p').lower()} where they are ({part})."


def _days_between(earlier: str | float | None, now: datetime) -> int | None:
    if not earlier:
        return None
    try:
        then = datetime.fromtimestamp(earlier, timezone.utc) if isinstance(earlier, (int, float)) else datetime.fromisoformat(earlier)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now.astimezone(timezone.utc).date() - then.astimezone(timezone.utc).date()).days


CONTEXT_MAX_CHARS = 60_000  # the whole context given to the live model
TRANSCRIPT_TAIL_CHARS = 4000  # the end of each recent conversation
PASSAGE_CHARS = 900
HEART_OPENING_CHARS = 500
NOTE_CHARS = 400


def context(retreat: dict | None, about: str, notes: str, history: dict | None = None, local_time: str | None = None) -> str:
    """What the companion knows: the person's notes and wishes, the time where they
    are, past conversations, and the retreat with what they've listened to, including
    what's new since they last talked."""
    now, when = _when(local_time)
    history = history or {}
    past = history.get("conversations", [])
    last = past[-1] if past else None
    last_time = (last.get("ended_at") or last.get("started_at")) if last else None
    parts = [COMPANION, prompts.BACKGROUND, "Right now: " + when]
    parts += _about_them(about, notes)
    parts += _past_conversations(history, last, last_time, now)
    if retreat and retreat.get("plan"):
        parts.append(_the_retreat(retreat, now, last, last_time))
    return "\n\n".join(parts)[:CONTEXT_MAX_CHARS]


def _about_them(about: str, notes: str) -> list[str]:
    parts = []
    if about.strip():
        parts.append(prompts.person_block(about))
    if notes.strip():
        parts.append("What they've said they want from this conversation companion:\n<wants>\n" + notes.strip() + "\n</wants>")
    return parts


def _past_conversations(history: dict, last: dict | None, last_time, now: datetime) -> list[str]:
    """When they last talked, the memory of older talks, and the latest transcripts."""
    parts = []
    if last:
        gap = _days_between(last_time, now)
        ago = "earlier today" if gap == 0 else "yesterday" if gap == 1 else f"{gap} days ago"
        parts.append(f"Your last conversation with them was {ago} ({str(last_time)[:10]}), about {last.get('retreat_title') or 'their prayer'}. "
                     "Pick up naturally from it if it helps; don't recite it back.")
    else:
        parts.append("This is your first conversation with them.")
    if history.get("memory"):
        parts.append("What you remember from earlier conversations (a summary):\n<memory>\n" + history["memory"] + "\n</memory>")
    recent = history.get("conversations", [])[-HISTORY_KEEP:]
    if recent:
        blocks = [f"[{str(c.get('started_at'))[:16]} · {c.get('retreat_title') or 'no retreat'} · {round((c.get('seconds') or 0) / 60)} min]\n"
                  + (c.get("transcript") or "")[-TRANSCRIPT_TAIL_CHARS:] for c in recent]
        parts.append("Your most recent conversations with them (transcripts, newest last):\n<recent>\n" + "\n\n".join(blocks) + "\n</recent>")
    return parts


def _the_retreat(retreat: dict, now: datetime, last: dict | None, last_time) -> str:
    """The retreat day by day: passage, grace, what they've prayed or started, their notes,
    and which days are new since the last conversation."""
    plan = retreat["plan"]
    lines = [f"The retreat they're making: {plan['title']}. {plan.get('summary', '')}"]
    if retreat.get("start_date"):
        day_no = _days_between(retreat["start_date"], now)
        if day_no is not None:
            lines.append(f"They started it on {retreat['start_date']}, so by the calendar today is day {day_no + 1} of {len(plan['days'])}.")
    since = []
    for d in plan["days"]:
        st = retreat["days"].get(str(d["day"]), {})
        listening = st.get("listening") or {}
        touched = st.get("prayed_at") or listening.get("updated_at")
        if touched and last_time and str(touched) > str(last_time):
            since.append(f"Day {d['day']}")
        lines += _day_lines(d, st)
    if last:
        lines.insert(1, ("Since your last conversation they have listened to or prayed " + ", ".join(since) + ".")
                     if since else "They haven't listened to any days of this retreat since your last conversation.")
    return "\n".join(lines)


def _day_lines(d: dict, st: dict) -> list[str]:
    lines = [f"\nDay {d['day']}: {d['title']} ({d.get('source_ref', '')}). Grace: {d.get('grace', '')}. Status: {_day_status(st)}.",
             f"Passage: {d.get('passage_text', '')[:PASSAGE_CHARS]}"]
    heart = (st.get("tracks", {}).get("heart") or {}).get("script", "")
    if heart:
        lines.append(f"The reflection they heard began: {heart[:HEART_OPENING_CHARS]}")
    journal = st.get("journal") or {}
    if journal.get("word"):
        lines.append(f"The word that stayed with them: {journal['word']}")
    if journal.get("note"):
        lines.append(f"Their note: {journal['note'][:NOTE_CHARS]}")
    return lines


def _day_status(st: dict) -> str:
    listening = st.get("listening") or {}
    if st.get("prayed_at"):
        return f"prayed ({st['prayed_at'][:10]})"
    if listening.get("parts_played"):
        return f"started ({str(listening.get('updated_at'))[:10]}), stopped at {listening.get('last_part') or 'part way'}"
    return "not listened to yet"


# ---------------------------------------------------------------- memory of past conversations
# Saved in the person's storage folder (Supabase Storage in production) as
# conversations.json: {"memory": summary of older talks, "conversations": [...]}.

HISTORY_KEEP = 3  # full transcripts given to the companion
HISTORY_CONDENSE_OVER = 16_000  # characters of older transcripts before they're folded into memory

REMEMBER = """You keep the memory of a prayer companion who has spoken with this person before. Combine the existing memory and the older conversation transcripts below into one updated memory, at most {limit} characters, written as plain notes: what they've shared about their life and prayer, graces and movements they noticed (consolation, desolation), questions they're carrying, words or images that mattered, what they said they'd bring to prayer next, and anything they asked you to remember or not to raise. Include dates where useful. Nothing else."""


def _history_path(user_id: str) -> str:
    return f"{user_id}/conversations.json"


async def load_history(user_id: str) -> dict:
    try:
        return json.loads(await store.get_file(_history_path(user_id)))
    except (StorageError, ValueError):
        return {"memory": "", "conversations": []}


async def save_history(user_id: str, history: dict) -> None:
    await store.put_file(_history_path(user_id), json.dumps(history).encode(), "application/json")


async def clear_history(user_id: str) -> None:
    await save_history(user_id, {"memory": "", "conversations": []})


async def _remember(user_id: str, full: bool) -> None:
    """Fold older transcripts into the memory summary once they get long."""
    history = await load_history(user_id)
    older = history["conversations"][:-HISTORY_KEEP]
    if sum(len(c.get("transcript") or "") for c in older) < HISTORY_CONDENSE_OVER:
        return
    from . import llm, pricing

    free_models = [m for m, _ in pricing.jetstream_models()]
    model = config.LLM_MODEL if full or not free_models else free_models[0]
    text = "Existing memory:\n" + (history.get("memory") or "(none)") + "\n\nOlder conversations:\n" + "\n\n".join(
        f"[{str(c.get('started_at'))[:16]}]\n{c.get('transcript') or ''}" for c in older)
    llm_log.tag(user_id=user_id, purpose="talk_memory")
    try:
        memory = await llm.condense_text(REMEMBER.format(limit=6000), text, pricing.Meter(model, await pricing.prices()))
    except Exception:
        log.warning("couldn't update conversation memory for %s", user_id)
        return
    history = await load_history(user_id)  # re-read: another conversation may have ended meanwhile
    keep = history["conversations"][len(older):]
    for c in history["conversations"][:len(older)]:
        c["transcript"] = None  # folded into memory; the summary line stays
    history["memory"] = memory.strip()[:7000]
    history["conversations"] = history["conversations"][:len(older)] + keep
    await save_history(user_id, history)


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


async def start(user, retreat: dict | None, about: str, notes: str, provider: str, voice: str, sdp: str | None,
                local_time: str | None = None) -> dict:
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
    instructions = context(retreat, about, notes, await load_history(user.id), local_time)
    if provider == "openai":
        if not sdp:
            raise TalkError(400, "Missing the browser's connection offer.")
        result = await _openai_session(instructions, voice, sdp)
    else:
        result = await _xai_session(instructions, voice)
    sid = result["session_id"]
    _sessions[sid] = {"user_id": user.id, "email": user.log_email,
                      "provider": provider, "voice": voice, "started": time.time(), "max": max_seconds,
                      "retreat_id": retreat["id"] if retreat else None,
                      "retreat_title": (retreat.get("plan") or {}).get("title") if retreat else None,
                      "instructions": instructions, "full": user.full}
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
    if transcript.strip():
        history = await load_history(user.id)
        history["conversations"].append({
            "id": session_id, "started_at": datetime.fromtimestamp(s["started"], timezone.utc).isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(), "retreat_id": s["retreat_id"],
            "retreat_title": s.get("retreat_title"), "provider": s["provider"], "voice": s["voice"],
            "seconds": seconds, "transcript": transcript[:60_000],
        })
        await save_history(user.id, history)
        asyncio.create_task(_remember(user.id, s["full"]))
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
# A WebSocket from the browser (wss://api.x.ai/v1/realtime), authenticated with a
# short-lived token this server mints, so the key never reaches the browser. The
# browser sends session.update with the instructions and voice, streams PCM16 audio
# and plays the replies (the protocol follows OpenAI Realtime). Time limits are
# enforced by the browser and the daily allowance.

XAI_WS = "wss://api.x.ai/v1/realtime"
XAI_FALLBACK_VOICES = {"eve": "Eve", "ara": "Ara", "rex": "Rex", "sal": "Sal", "leo": "Leo"}
_xai_voice_cache: dict = {}


async def xai_voices() -> dict[str, str]:
    """xAI's voice list (GET /v1/tts/voices, cached for a day), or a short fallback."""
    if _xai_voice_cache.get("at", 0) > time.time() - 86400:
        return _xai_voice_cache["voices"]
    voices = dict(XAI_FALLBACK_VOICES)
    if config.XAI_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.get("https://api.x.ai/v1/tts/voices", headers={"Authorization": f"Bearer {config.XAI_API_KEY}"})
            items = r.json().get("voices") or r.json().get("data") or []
            found = {}
            for v in items:
                vid = (v.get("voice_id") or v.get("id") or v.get("name") or "").lower()
                if vid:
                    desc = v.get("description") or v.get("style") or ""
                    found[vid] = f"{(v.get('name') or vid).title()}{f' ({desc})' if desc else ''}"
            if found:
                voices = found
        except Exception:
            log.warning("couldn't list xAI voices; using the fallback list")
    _xai_voice_cache.update(at=time.time(), voices=voices)
    XAI_VOICES.clear()
    XAI_VOICES.update(voices)
    return voices


async def _xai_session(instructions: str, voice: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(
                "https://api.x.ai/v1/realtime/client_secrets",
                headers={"Authorization": f"Bearer {config.XAI_API_KEY}"},
                json={"expires_after": {"seconds": 300}},
            )
    except httpx.HTTPError as exc:
        raise TalkError(502, "Couldn't reach the Grok voice service.") from exc
    if r.status_code >= 400:
        log.warning("xai client secret failed: %s %s", r.status_code, r.text[:200])
        raise TalkError(502, "The Grok voice service couldn't start a session. Try again in a moment.")
    token = r.json().get("value")
    if not token:
        raise TalkError(502, "The Grok voice service didn't return a session token.")
    session = {
        "instructions": instructions,
        "voice": voice,
        "turn_detection": {"type": "server_vad", "threshold": 0.6, "silence_duration_ms": 700, "prefix_padding_ms": 300},
        "audio": {
            "input": {"format": {"type": "audio/pcm", "rate": 24000}, "transcription": {"model": "grok-transcribe", "language_hint": "en"}},
            "output": {"format": {"type": "audio/pcm", "rate": 24000}},
        },
    }
    return {"session_id": _new_id(), "token": token, "ws_url": f"{XAI_WS}?model={config.XAI_VOICE_MODEL}", "session": session}


def _new_id() -> str:
    return "local_" + uuid.uuid4().hex
