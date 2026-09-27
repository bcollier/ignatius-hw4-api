"""Your own Examen (full accounts): Claude writes an end-of-day Examen around the
person's life (their work, the people at home, the places they pass through) from
their About me notes and what they add about their days, and it's recorded once in a
British voice. Kept in their folder as examen/examen.json plus one clip per spoken
segment; they pray the same recording each night until they make a new one."""

import asyncio
import json
import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config, llm, llm_log, pricing, profile, prompts, tts
from .storage import StorageError, store

log = logging.getLogger(__name__)

VOICES = {
    "deluxe": ("JBFqnCBsd6RMkjVDRZzb", "George, ElevenLabs"),
    "standard": ("en-GB-RyanNeural", "Ryan, Microsoft (free)"),
}
MAX_DAYS_TEXT = 4_000
STALE_SECONDS = 20 * 60  # a "making" left behind by a restart counts as failed after this
BOOTED = time.time()  # when this server started; a job begun before it was cut off by a restart
MAX_RESTARTS = 2
SYSTEM = prompts._prompt("my_examen")

SEGMENT = {
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
}
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "segments": {"type": "array", "items": SEGMENT},
    },
    "required": ["title", "summary", "segments"],
    "additionalProperties": False,
}

_tasks: set[asyncio.Task] = set()  # keep references so running tasks aren't garbage collected
_running: set[str] = set()  # people whose Examen this server is making right now


def _path(user_id: str) -> str:
    return f"{user_id}/examen/examen.json"


async def load(user_id: str) -> dict:
    try:
        state = json.loads(await store.get_file(_path(user_id)))
    except (StorageError, ValueError):
        return {"status": "none"}
    if state.get("status") == "making" and user_id not in _running:
        if state.get("started", 0) < BOOTED and state.get("restarts", 0) < MAX_RESTARTS:
            # Begun on a server that has since restarted (an update, say): start it again.
            log.warning("restarting an interrupted Examen for %s", user_id)
            state["restarts"] = state.get("restarts", 0) + 1
            return await _launch(user_id, state.get("email"), state)
        if time.time() - state.get("started", 0) > STALE_SECONDS or state.get("started", 0) < BOOTED:
            state["status"], state["error"] = "failed", "It stopped partway (the server restarted). Please try again."
    return state


async def _save(user_id: str, state: dict) -> None:
    await store.put_file(_path(user_id), json.dumps(state, ensure_ascii=False).encode(), "application/json")


async def view(user_id: str) -> dict:
    """The person's Examen with a playable link for each clip."""
    state = await load(user_id)
    session = state.get("session")
    if session:
        paths = [g["audio"]["path"] for g in session["segments"] if g.get("audio")]
        urls = await store.urls(paths)
        for g in session["segments"]:
            if g.get("audio"):
                g["audio"]["file"] = urls.get(g["audio"]["path"], "")
    return state


async def start(user_id: str, email: str | None, days: str, voice: str) -> dict:
    """Begin writing and recording; returns at once with status "making"."""
    previous = await load(user_id)
    if previous.get("status") == "making":
        return previous
    state = {**previous, "status": "making", "error": None, "restarts": 0,
             "days": days[:MAX_DAYS_TEXT], "voice": voice, "email": email}
    return await _launch(user_id, email, state)


async def _launch(user_id: str, email: str | None, state: dict) -> dict:
    state["started"] = time.time()
    await _save(user_id, state)
    _running.add(user_id)
    task = asyncio.create_task(_make(user_id, email, state))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    task.add_done_callback(lambda _: _running.discard(user_id))
    return state


async def _make(user_id: str, email: str | None, state: dict) -> None:
    llm_log.tag(user_id=user_id, email=email, purpose="my_examen")
    try:
        await profile.use_for_job(user_id)
        await profile.use_agents(user_id)
        model = (await profile.agent_settings(user_id))["models"].get("my_examen") or config.LLM_MODEL
        session, script_usd = await _write(state["days"], model)
        voice_usd = await _record(user_id, session, state["voice"])
        state.update(status="ready", session=session, made_at=datetime.now(timezone.utc).isoformat(),
                     usd={"script": round(script_usd, 4), "voice": round(voice_usd, 4)})
    except Exception as exc:  # shown to the person; details are in the server log
        log.exception("couldn't make the Examen for %s", user_id)
        state.update(status="failed", error=str(getattr(exc, "message", None) or exc)[:300])
    await _save(user_id, state)


async def _write(days: str, model: str) -> tuple[dict, float]:
    """Claude writes the session around the person's life."""
    meter = pricing.Meter(model, await pricing.prices())
    added = days.strip() or "(They haven't added anything; use their notes, if any.)"
    message = await llm._call(
        meter, system=prompts.custom("my_examen", SYSTEM), max_tokens=16000,
        messages=[{"role": "user", "content": f"<about_their_days>\n{added}\n</about_their_days>\n\nWrite their Examen."}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
    session = llm._parse_json(llm._text(message))
    session["id"] = "my-examen"
    return session, meter.usd


async def _record(user_id: str, session: dict, tier: str) -> float:
    """Record each spoken segment and upload it; returns the voice cost."""
    voice, label = VOICES.get(tier, VOICES["deluxe"])
    session["voices"] = {tier: label}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")  # new paths, so no old clip is cached
    spoken = [(n, g) for n, g in enumerate(session["segments"], start=1) if g["kind"] == "speak"]

    async def one(n: int, seg: dict, folder: Path) -> None:
        local = folder / f"{n:02d}.mp3"
        seconds = await tts.synthesize(seg["text"], voice, local)
        path = f"{user_id}/examen/{stamp}/{n:02d}.mp3"
        await store.put_file(path, local.read_bytes(), "audio/mpeg")
        seg["audio"] = {"path": path, "seconds": seconds, "tier": tier}

    with tempfile.TemporaryDirectory() as tmp:
        await asyncio.gather(*(one(n, g, Path(tmp)) for n, g in spoken))
    return pricing.tts_usd(sum(len(g["text"]) for _, g in spoken), tts.tier_of(voice))
