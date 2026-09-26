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

from . import config, llm, llm_log, pricing, prompts, tts
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
    for d in retreat["days"].values():
        for group in ("tracks", "guide"):
            paths += [t["path"] for t in d.get(group, {}).values() if t.get("status") == "ready"]
    urls = await store.urls(paths) if paths else {}
    view["images"] = [{**img, "url": urls.get(img["path"])} for img in retreat["images"]]

    def with_urls(clips: dict) -> dict:
        return {k: {**t, "url": urls.get(t.get("path"))} for k, t in clips.items()}

    view["days"] = {
        n: {**d, "tracks": with_urls(d["tracks"]), "guide": with_urls(d.get("guide", {}))}
        for n, d in retreat["days"].items()
    }
    return view


async def create_retreat(user_id: str, filename: str, source: Extracted, plan_prompt: str, model: str) -> dict:
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
        "model": model,
        "costs": {},
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
    meter = pricing.Meter(retreat["model"], await pricing.prices())
    llm_log.tag(user_id=retreat["user_id"], retreat_id=retreat["id"], purpose="plan")
    async with _jobs:
        try:
            plan = await llm.plan_retreat(source, retreat["filename"], plan_prompt, meter)
        except llm.LLMError as exc:
            retreat.update(status="failed", error=str(exc), costs={"plan": meter.summary()})
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
        str(d["day"]): {"status": "idle", "error": None, "tracks": {}, "guide": {}, "cost": None} for d in plan["days"]
    }
    retreat["status"] = "ready"
    retreat["costs"] = {"plan": meter.summary()}
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


SECTIONS = ("guide", "reading", "heart", "deep")  # each can have its own voice


async def start_day_build(
    retreat: dict, day_no: int, voices: dict, heart_prompt: str, deep_prompt: str, guide: dict, keep_scripts: bool, model: str
) -> dict:
    for section in SECTIONS:
        tts.tier_of(voices[section])  # raises TTSError for an unknown voice
    state = retreat["days"][str(day_no)]
    old = {k: t for k, t in state.get("tracks", {}).items() if t.get("script")}
    if keep_scripts and not all(name in old for name in ("heart", "deep")):
        keep_scripts = False  # nothing to keep yet; write them
    state.update(
        status="building",
        error=None,
        voices=voices,
        tracks={name: {"status": "waiting"} for name in TRACKS},
        guide={name: {"status": "waiting"} for name in guide},
        cost=None,
    )
    await save(retreat)
    meter = pricing.Meter(model, await pricing.prices())
    spawn(retreat, _build_day(retreat, day_no, heart_prompt, deep_prompt, guide, old if keep_scripts else {}, meter))
    return state


async def _build_day(
    retreat: dict, day_no: int, heart_prompt: str, deep_prompt: str, guide: dict, kept: dict, meter: pricing.Meter
) -> None:
    state = retreat["days"][str(day_no)]
    day = retreat["plan"]["days"][day_no - 1]
    voices = state["voices"]
    image = retreat["images"][day["image_index"]]["description"] if day["image_index"] >= 0 else None
    context = prompts.day_context(retreat["plan"]["title"], day, image)

    def words_for(section: str) -> int:
        return int(tts.max_chars(voices[section]) / 6 * 0.85)  # about six characters per word with spaces

    async def record(group: str, name: str, script: str, voice: str, **extra) -> None:
        clip = state[group][name]
        script, trimmed = fit(script, tts.max_chars(voice))
        clip.update(status="speaking", script=script, characters=len(script), trimmed=trimmed, voice=voice, **extra)
        await save(retreat)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "clip.mp3"
            seconds = await tts.synthesize(script, voice, out)
            path = f"{retreat['user_id']}/{retreat['id']}/day{day_no}_{name}.mp3"
            await store.put_file(path, out.read_bytes(), "audio/mpeg")
        clip.update(status="ready", path=path, seconds=seconds)
        await save(retreat)

    llm_log.tag(user_id=retreat["user_id"], retreat_id=retreat["id"], day=day_no)

    async def heart() -> None:
        llm_log.tag(purpose="heart")
        if "heart" in kept:
            return await record("tracks", "heart", kept["heart"]["script"], voices["heart"])
        state["tracks"]["heart"]["status"] = "writing"
        await record("tracks", "heart", await llm.write_heart(context, heart_prompt, words_for("heart"), meter), voices["heart"])

    async def deep() -> None:
        llm_log.tag(purpose="deep")
        if "deep" in kept:
            k = kept["deep"]
            return await record("tracks", "deep", k["script"], voices["deep"], sources=k.get("sources", []), web_search=k.get("web_search"))
        state["tracks"]["deep"]["status"] = "writing"
        script, sources, searched = await llm.write_deep(context, deep_prompt, words_for("deep"), meter)
        await record("tracks", "deep", script, voices["deep"], sources=sources, web_search=searched)

    reading = re.sub(r"\n{3,}", "\n\n", day["passage_text"]).strip()
    jobs = {("tracks", "reading"): record("tracks", "reading", reading, voices["reading"]),
            ("tracks", "heart"): heart(),
            ("tracks", "deep"): deep()}
    for name, template in guide.items():
        jobs[("guide", name)] = record("guide", name, prompts.guide_text(template, day), voices["guide"])

    async with _jobs:
        results = await asyncio.gather(*jobs.values(), return_exceptions=True)

    errors = []
    for (group, name), result in zip(jobs, results):
        if isinstance(result, Exception):
            known = isinstance(result, (llm.LLMError, tts.TTSError, StorageError))
            if not known:
                log.error("day build failed", exc_info=result)
            message = str(result) if known else "Unexpected error."
            state[group][name].update(status="failed", error=message)
            errors.append(f"{name}: {message}")
    state["status"] = "failed" if errors else "ready"
    state["error"] = "; ".join(dict.fromkeys(errors)) or None
    state["cost"] = _day_cost(state, meter)
    await save(retreat)


def _day_cost(state: dict, meter: pricing.Meter) -> dict:
    """What this build spent: model calls from token usage, voices by character."""
    chars = {"free": 0, "premium": 0}
    for group in ("tracks", "guide"):
        for clip in state.get(group, {}).values():
            if clip.get("status") == "ready":
                try:
                    tier = tts.tier_of(clip["voice"])
                except tts.TTSError:  # a premium voice after the ElevenLabs key was removed
                    tier = "premium"
                chars[tier] += clip.get("characters", 0)
    llm_cost = meter.summary()
    voice_usd = pricing.tts_usd(chars["premium"], "premium")
    return {
        "llm": llm_cost,
        "voice_characters": chars,
        "voice_usd": voice_usd,
        "total_usd": round(llm_cost["usd"] + voice_usd, 4),
    }
