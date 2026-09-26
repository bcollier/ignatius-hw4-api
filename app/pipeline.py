"""Background jobs: plan a retreat from an upload, then build a day's three audio tracks.

Retreats being worked on are held in memory and saved to storage (Supabase, or
disk locally) at each step, so they survive restarts and can be opened from any
device the owner signs in on.

Jobs survive restarts. A running job saves a heartbeat every HEARTBEAT seconds.
If a retreat is found mid-job with no running task here and a heartbeat older than
STALE seconds (a redeploy or crash; during Render's zero-downtime deploys the old
server may still be finishing, hence the wait), the job is resumed: planning
restarts from the saved source, and a day build continues, keeping finished
recordings and already-written scripts. After MAX_RESUMES it gives up.
"""

import asyncio
import json
import logging
import re
import tempfile
import time
import uuid
from pathlib import Path

from . import config, llm, llm_log, pricing, prompts, search, series, tts
from .extract import Extracted, Image
from .storage import StorageError, store

log = logging.getLogger(__name__)

TRACKS = ("reading", "heart", "deep")

active: dict[str, dict] = {}  # retreats with a job running, by id
HEARTBEAT = 20
STALE = 90
MAX_RESUMES = 2
_jobs = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)
_tasks: set[asyncio.Task] = set()  # keep references so running tasks aren't garbage collected


def _busy(retreat: dict) -> bool:
    return retreat["status"] == "planning" or any(d["status"] == "building" for d in retreat["days"].values())


def spawn(retreat: dict, coro) -> None:
    active[retreat["id"]] = retreat
    retreat["heartbeat"] = time.time()
    task = asyncio.create_task(_with_heartbeat(retreat, coro))
    _tasks.add(task)

    def done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if not _busy(retreat):
            active.pop(retreat["id"], None)

    task.add_done_callback(done)


async def _with_heartbeat(retreat: dict, coro) -> None:
    """Run the job, saving a heartbeat while it runs, so another server can tell a
    live job from one that died with its server."""

    async def beat():
        while True:
            await asyncio.sleep(HEARTBEAT)
            retreat["heartbeat"] = time.time()
            await save(retreat)

    beater = asyncio.create_task(beat())
    try:
        await coro
    finally:
        beater.cancel()


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
    if _busy(retreat) and time.time() - retreat.get("heartbeat", 0) > STALE:
        await _resume(retreat)
    return retreat


async def _resume(retreat: dict) -> None:
    """Pick up a job whose server went away (see the module docstring)."""
    retreat["resumes"] = retreat.get("resumes", 0) + 1
    if retreat["resumes"] > MAX_RESUMES:
        if retreat["status"] == "planning":
            retreat.update(status="failed", error="Planning was interrupted too many times. Upload the document again.")
        for state in retreat["days"].values():
            if state["status"] == "building":
                state.update(status="failed", error="The build was interrupted too many times. Build this day again.")
        return await save(retreat)

    log.warning("resuming interrupted job for retreat %s (attempt %s)", retreat["id"], retreat["resumes"])
    llm_log.tag(email=retreat.get("owner_email"))
    if retreat["status"] == "planning":
        source = await _load_source(retreat)
        if source is None:
            retreat.update(status="failed", error="Planning was interrupted by a server restart. Upload the document again.")
            return await save(retreat)
        spawn(retreat, _plan(retreat, source, retreat.get("plan_prompt") or prompts.PLAN_INSTRUCTIONS))
        return
    for day_no, state in retreat["days"].items():
        if state["status"] == "building":
            p = state.get("params")
            if not p:  # built before resuming existed
                state.update(status="failed", error="The build was interrupted by a server restart. Build this day again.")
                await save(retreat)
                continue
            kept = {k: t for k, t in state["tracks"].items() if t.get("script") and k in ("heart", "deep")}
            meter = pricing.Meter(p["model"], await pricing.prices())
            spawn(retreat, _build_day(retreat, int(day_no), p["heart_prompt"], p["deep_prompt"], p["guide"], kept, meter,
                                      p.get("search_provider")))


def _source_path(retreat: dict, name: str) -> str:
    return f"{retreat['user_id']}/{retreat['id']}/{name}"


async def _save_source(retreat: dict, source: Extracted) -> None:
    """Keep what planning needs, so an interrupted plan can restart."""
    meta = {"kind": source.kind, "text": source.text, "page_count": source.page_count, "truncated": source.truncated,
            "scanned": [img.page for img in source.scanned_pages]}
    await store.put_file(_source_path(retreat, "source.json"), json.dumps(meta).encode(), "application/json")
    for i, img in enumerate(source.scanned_pages):
        await store.put_file(_source_path(retreat, f"scan{i}.png"), img.data, img.mime)


async def _load_source(retreat: dict) -> Extracted | None:
    try:
        meta = json.loads(await store.get_file(_source_path(retreat, "source.json")))
        images = [Image(await store.get_file(img["path"]), "image/jpeg", 0, 0, img.get("page")) for img in retreat["images"]]
        scans = [Image(await store.get_file(_source_path(retreat, f"scan{i}.png")), "image/png", 0, 0, page)
                 for i, page in enumerate(meta.get("scanned", []))]
    except (StorageError, ValueError):
        return None
    return Extracted(kind=meta["kind"], text=meta["text"], page_count=meta["page_count"], images=images,
                     scanned_pages=scans, truncated=meta.get("truncated", False))


async def public_view(retreat: dict) -> dict:
    """The retreat as the API returns it, with file paths turned into URLs."""
    view = {k: v for k, v in retreat.items() if k not in ("user_id", "owner_email", "plan_prompt")}
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


async def create_retreat(
    user_id: str, filename: str, source: Extracted, plan_prompt: str, model: str, email: str | None = None,
    series_ids: list[str] | None = None,
) -> dict:
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
        "plan_prompt": plan_prompt if plan_prompt != prompts.PLAN_INSTRUCTIONS else None,
        "owner_email": email,
        "model": model,
        "series": series_ids or [],  # earlier retreats, oldest first
        "series_info": None,
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
    await _save_source(retreat, source)
    await store.save(retreat)
    spawn(retreat, _plan(retreat, source, plan_prompt))
    return retreat


async def series_context(retreat: dict, model: str) -> str:
    """The earlier retreats in this one's series, as text for the model (see series.py)."""
    if not retreat.get("series"):
        return ""
    previous = []
    for rid in retreat["series"]:
        try:
            r = await store.load(rid)
        except StorageError:
            r = None
        if r and r["user_id"] == retreat["user_id"]:  # deleted or someone else's: skip
            previous.append(r)
    text, stats = series.context(previous, series.budget_for(model))
    retreat["series_info"] = stats
    return text


async def _plan(retreat: dict, source: Extracted, plan_prompt: str) -> None:
    meter = pricing.Meter(retreat["model"], await pricing.prices())
    llm_log.tag(user_id=retreat["user_id"], retreat_id=retreat["id"], purpose="plan")
    series_text = await series_context(retreat, retreat["model"])
    async with _jobs:
        try:
            plan = await llm.plan_retreat(source, retreat["filename"], plan_prompt, meter, series_text)
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
    retreat: dict, day_no: int, voices: dict, heart_prompt: str, deep_prompt: str, guide: dict, keep_scripts: bool, model: str,
    search_provider: str | None = None,
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
        params={"heart_prompt": heart_prompt, "deep_prompt": deep_prompt, "guide": guide, "model": model,
                "search_provider": search_provider},
    )
    await save(retreat)
    meter = pricing.Meter(model, await pricing.prices())
    spawn(retreat, _build_day(retreat, day_no, heart_prompt, deep_prompt, guide, old if keep_scripts else {}, meter, search_provider))
    return state


async def _build_day(
    retreat: dict, day_no: int, heart_prompt: str, deep_prompt: str, guide: dict, kept: dict, meter: pricing.Meter,
    search_provider: str | None = None,
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
    series_text = await series_context(retreat, meter.model)

    async def heart() -> None:
        llm_log.tag(purpose="heart")
        if "heart" in kept:
            return await record("tracks", "heart", kept["heart"]["script"], voices["heart"])
        state["tracks"]["heart"]["status"] = "writing"
        await record("tracks", "heart", await llm.write_heart(context, heart_prompt, words_for("heart"), meter, series_text), voices["heart"])

    async def deep() -> None:
        llm_log.tag(purpose="deep")
        if "deep" in kept:
            k = kept["deep"]
            return await record("tracks", "deep", k["script"], voices["deep"], sources=k.get("sources", []), web_search=k.get("web_search"))
        state["tracks"]["deep"]["status"] = "writing"
        script, sources, searched = await llm.write_deep(context, deep_prompt, words_for("deep"), meter, search_provider, series_text)
        # For Jetstream models `searched` names the research service that answered.
        await record("tracks", "deep", script, voices["deep"], sources=sources, web_search=bool(searched),
                     research=search.PROVIDERS.get(searched) if isinstance(searched, str) else None)

    reading = re.sub(r"\n{3,}", "\n\n", day["passage_text"]).strip()
    work = {("tracks", "reading"): lambda: record("tracks", "reading", reading, voices["reading"]),
            ("tracks", "heart"): heart,
            ("tracks", "deep"): deep}
    for name, template in guide.items():
        work[("guide", name)] = lambda name=name, template=template: record(
            "guide", name, prompts.guide_text(template, day), voices["guide"])
    # A resumed build skips whatever finished before the interruption.
    jobs = {key: make() for key, make in work.items() if state[key[0]].get(key[1], {}).get("status") != "ready"}

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
