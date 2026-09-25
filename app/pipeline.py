"""Background jobs: plan a retreat from an upload, then build a day's three audio tracks.

Retreats being worked on are held in memory and saved to storage (Supabase, or
disk locally) at each step, so they survive restarts and can be opened from any
device the owner signs in on.
"""

import asyncio
import logging
import re
import tempfile
import time
import uuid
from pathlib import Path

from . import config, llm, prompts, tts
from .extract import Extracted
from .storage import StorageError, store

log = logging.getLogger(__name__)

TRACKS = ("reading", "heart", "deep")

active: dict[str, dict] = {}  # retreats with a job running, by id
_jobs = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)
_tasks: set[asyncio.Task] = set()  # keep references so running tasks aren't garbage collected


def _busy(retreat: dict) -> bool:
    return retreat["status"] == "planning" or any(d["status"] == "building" for d in retreat["days"].values())


def spawn(retreat: dict, coro) -> None:
    active[retreat["id"]] = retreat
    task = asyncio.create_task(coro)
    _tasks.add(task)

    def done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if not _busy(retreat):
            active.pop(retreat["id"], None)

    task.add_done_callback(done)


async def save(retreat: dict) -> None:
    try:
        await store.save(retreat)
    except StorageError:
        log.exception("couldn't save retreat %s", retreat["id"])


async def get(retreat_id: str) -> dict | None:
    if retreat_id in active:
        return active[retreat_id]
    try:
        retreat = await store.load(retreat_id)
    except StorageError:
        return None
    if retreat is None:
        return None
    # A job that was running when the server restarted will never finish.
    if _busy(retreat):
        if retreat["status"] == "planning":
            retreat.update(status="failed", error="Planning was interrupted by a server restart. Upload the document again.")
        for state in retreat["days"].values():
            if state["status"] == "building":
                state.update(status="failed", error="The build was interrupted by a server restart. Build this day again.")
        await save(retreat)
    return retreat


async def public_view(retreat: dict) -> dict:
    """The retreat as the API returns it, with file paths turned into URLs."""
    view = {k: v for k, v in retreat.items() if k != "user_id"}
    paths = [img["path"] for img in retreat["images"]]
    paths += [t["path"] for d in retreat["days"].values() for t in d["tracks"].values() if t.get("status") == "ready"]
    urls = await store.urls(paths) if paths else {}
    view["images"] = [{**img, "url": urls.get(img["path"])} for img in retreat["images"]]
    view["days"] = {
        n: {**d, "tracks": {k: {**t, "url": urls.get(t.get("path"))} for k, t in d["tracks"].items()}}
        for n, d in retreat["days"].items()
    }
    return view


async def create_retreat(user_id: str, filename: str, source: Extracted, plan_prompt: str) -> dict:
    retreat_id = str(uuid.uuid4())
    images = []
    for i, img in enumerate(source.images):
        path = f"{user_id}/{retreat_id}/image{i}.jpg"
        await store.put_file(path, img.data, img.mime)
        images.append({"index": i, "path": path, "page": img.page, "description": ""})

    retreat = {
        "id": retreat_id,
        "user_id": user_id,
        "filename": filename,
        "created_at": time.time(),
        "status": "planning",
        "error": None,
        "custom_plan_prompt": plan_prompt != prompts.PLAN_INSTRUCTIONS,
        "source": {
            "kind": source.kind,
            "pages": source.page_count,
            "characters": len(source.text),
            "images": len(source.images),
            "scanned_pages": len(source.scanned_pages),
            "truncated": source.truncated,
        },
        "images": images,
        "plan": None,
        "days": {},
    }
    await store.save(retreat)
    spawn(retreat, _plan(retreat, source, plan_prompt))
    return retreat


async def _plan(retreat: dict, source: Extracted, plan_prompt: str) -> None:
    async with _jobs:
        try:
            plan = await llm.plan_retreat(source, retreat["filename"], plan_prompt)
        except llm.LLMError as exc:
            retreat.update(status="failed", error=str(exc))
            return await save(retreat)
        except Exception:
            log.exception("planning failed")
            retreat.update(status="failed", error="Something went wrong while planning the retreat.")
            return await save(retreat)
    for note in plan.get("images", []):
        if 0 <= note.get("index", -1) < len(retreat["images"]):
            retreat["images"][note["index"]]["description"] = note["description"]
    retreat["plan"] = plan
    retreat["days"] = {
        str(d["day"]): {"status": "idle", "error": None, "tier": None, "voice": None, "tracks": {}} for d in plan["days"]
    }
    retreat["status"] = "ready"
    await save(retreat)


# ---------------------------------------------------------------- building a day


def fit(text: str, max_chars: int) -> tuple[str, bool]:
    """Trim to max_chars at a sentence boundary. Returns (text, trimmed)."""
    text = text.strip()
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    end = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind("? "), cut.rfind("! "))
    return (cut[: end + 1] if end > max_chars // 2 else cut).strip(), True


async def start_day_build(retreat: dict, day_no: int, tier: str, voice: str, heart_prompt: str, deep_prompt: str) -> dict:
    tts.validate(tier, voice)
    state = retreat["days"][str(day_no)]
    state.update(
        status="building",
        error=None,
        tier=tier,
        voice=voice,
        tracks={name: {"status": "waiting"} for name in TRACKS},
    )
    await save(retreat)
    spawn(retreat, _build_day(retreat, day_no, heart_prompt, deep_prompt))
    return state


async def _build_day(retreat: dict, day_no: int, heart_prompt: str, deep_prompt: str) -> None:
    state = retreat["days"][str(day_no)]
    day = retreat["plan"]["days"][day_no - 1]
    tier, voice = state["tier"], state["voice"]
    max_chars = tts.tiers()[tier]["max_chars"]
    words = int(max_chars / 6 * 0.85)  # English averages about six characters per word with spaces
    image = retreat["images"][day["image_index"]]["description"] if day["image_index"] >= 0 else None
    context = prompts.day_context(retreat["plan"]["title"], day, image)

    async def speak(name: str, script: str, **extra) -> None:
        track = state["tracks"][name]
        script, trimmed = fit(script, max_chars)
        track.update(status="speaking", script=script, characters=len(script), trimmed=trimmed, **extra)
        await save(retreat)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "track.mp3"
            await tts.synthesize(script, tier, voice, out)
            path = f"{retreat['user_id']}/{retreat['id']}/day{day_no}_{name}.mp3"
            await store.put_file(path, out.read_bytes(), "audio/mpeg")
        track.update(status="ready", path=path)
        await save(retreat)

    async def heart() -> None:
        state["tracks"]["heart"]["status"] = "writing"
        await speak("heart", await llm.write_heart(context, heart_prompt, words))

    async def deep() -> None:
        state["tracks"]["deep"]["status"] = "writing"
        script, sources, searched = await llm.write_deep(context, deep_prompt, words)
        await speak("deep", script, sources=sources, web_search=searched)

    reading = re.sub(r"\n{3,}", "\n\n", f"Day {day_no}. {day['title']}.\n\n{day['passage_text']}")
    async with _jobs:
        results = await asyncio.gather(speak("reading", reading), heart(), deep(), return_exceptions=True)

    errors = []
    for name, result in zip(TRACKS, results):
        if isinstance(result, Exception):
            known = isinstance(result, (llm.LLMError, tts.TTSError, StorageError))
            if not known:
                log.error("day build failed", exc_info=result)
            message = str(result) if known else "Unexpected error."
            state["tracks"][name].update(status="failed", error=message)
            errors.append(f"{name}: {message}")
    state["status"] = "failed" if errors else "ready"
    state["error"] = "; ".join(errors) or None
    await save(retreat)
