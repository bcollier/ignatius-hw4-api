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

from . import config, llm, llm_log, pricing, profile, prompts, search, series, tts
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
    return retreat["status"] in ("planning", "building") or any(
        d["status"] in ("building", "queued") for d in retreat["days"].values()
    )


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
            if state["status"] in ("building", "queued"):
                state.update(status="failed", error="Making this day was interrupted too many times. Try again.")
        if retreat["status"] == "building":
            _finish_building(retreat)
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
    if retreat["status"] == "building" and retreat.get("build_options"):
        spawn(retreat, _build_all(retreat))  # continues from the first day that isn't done
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
    if view.get("plan"):  # retreats planned before image_indexes existed
        view["plan"] = {**view["plan"], "days": [
            {**d, "image_indexes": d.get("image_indexes", [d["image_index"]] if d.get("image_index", -1) >= 0 else [])}
            for d in view["plan"]["days"]
        ]}
    return view


async def create_retreat(
    user_id: str, filename: str, source: Extracted, plan_prompt: str, model: str, email: str | None = None,
    series_ids: list[str] | None = None, build_options: dict | None = None, start_date: str | None = None,
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
        # With build options, every day is made right after planning (see _build_all).
        "build_options": build_options,
        "progress": None,
        "start_date": start_date,
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
    await profile.use_for_job(retreat["user_id"])  # "user info.md" informs every call
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
    first = "queued" if retreat.get("build_options") else "idle"
    retreat["days"] = {
        str(d["day"]): {"status": first, "error": None, "tracks": {}, "guide": {}, "cost": None} for d in plan["days"]
    }
    retreat["costs"] = {"plan": meter.summary()}
    if retreat.get("build_options"):
        retreat["status"] = "building"
        retreat["progress"] = {"done": 0, "total": len(plan["days"]), "current_day": None, "failed": []}
        await save(retreat)
        await _build_all(retreat)
    else:
        retreat["status"] = "ready"
        await save(retreat)


async def _build_all(retreat: dict) -> None:
    """Make every day, one after another, with the options chosen at upload. A day that
    fails is left failed and the rest continue. After a restart this picks up where it
    stopped: finished days are skipped, a day caught mid-build keeps what it finished."""
    opts = retreat["build_options"]
    for day_no in sorted(retreat["days"], key=int):
        state = retreat["days"][day_no]
        if state["status"] not in ("queued", "building"):
            continue
        retreat["progress"]["current_day"] = int(day_no)
        try:
            if state["status"] == "queued":
                kept, meter = await _prepare_day(retreat, int(day_no), opts, keep_scripts=False)
            else:  # resuming a day that was mid-build
                kept = {k: t for k, t in state["tracks"].items() if t.get("script") and k in ("heart", "deep")}
                kept["guide"] = {k: c["script"] for k, c in state.get("guide", {}).items() if c.get("script")}
                meter = pricing.Meter(opts["write_model"], await pricing.prices())
            await _build_day(retreat, int(day_no), opts["heart_prompt"], opts["deep_prompt"], opts["guide"], kept, meter,
                             opts.get("search_provider"))
        except Exception:  # one bad day must not stop the rest
            log.exception("making day %s failed", day_no)
            state.update(status="failed", error=state.get("error") or "Something went wrong making this day.")
        _count_progress(retreat)
        await save(retreat)
    _finish_building(retreat)
    await save(retreat)


def _count_progress(retreat: dict) -> None:
    days = retreat["days"].values()
    retreat["progress"].update(
        done=sum(1 for d in days if d["status"] in ("ready", "failed")),
        failed=[int(n) for n, d in retreat["days"].items() if d["status"] == "failed"],
    )


def _finish_building(retreat: dict) -> None:
    if retreat.get("progress"):
        _count_progress(retreat)
        retreat["progress"]["current_day"] = None
    retreat["status"] = "ready"


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


async def _prepare_day(retreat: dict, day_no: int, opts: dict, keep_scripts: bool) -> tuple[dict, pricing.Meter]:
    """Reset a day for building with the given options; returns (scripts to keep, meter)."""
    voices = opts["voices"]
    for section in SECTIONS:
        tts.tier_of(voices[section])  # raises TTSError for an unknown voice
    state = retreat["days"][str(day_no)]
    old = {k: t for k, t in state.get("tracks", {}).items() if t.get("script")}
    old["guide"] = {k: c["script"] for k, c in state.get("guide", {}).items() if c.get("script")}
    if keep_scripts and not all(name in old for name in ("heart", "deep")):
        keep_scripts = False  # nothing to keep yet; write them
    state.update(
        status="building",
        error=None,
        voices=voices,
        tracks={name: {"status": "waiting"} for name in TRACKS},
        guide={name: {"status": "waiting"} for name in opts["guide"]},
        cost=None,
        params={"heart_prompt": opts["heart_prompt"], "deep_prompt": opts["deep_prompt"], "guide": opts["guide"],
                "model": opts["write_model"], "search_provider": opts.get("search_provider")},
    )
    await save(retreat)
    return (old if keep_scripts else {}), pricing.Meter(opts["write_model"], await pricing.prices())


async def start_day_build(
    retreat: dict, day_no: int, voices: dict, heart_prompt: str, deep_prompt: str, guide: dict, keep_scripts: bool, model: str,
    search_provider: str | None = None,
) -> dict:
    """Rebuild one day (Rewrite, Re-record, Try again)."""
    opts = {"voices": voices, "heart_prompt": heart_prompt, "deep_prompt": deep_prompt, "guide": guide,
            "write_model": model, "search_provider": search_provider}
    kept, meter = await _prepare_day(retreat, day_no, opts, keep_scripts)
    spawn(retreat, _build_day(retreat, day_no, heart_prompt, deep_prompt, guide, kept, meter, search_provider))
    return retreat["days"][str(day_no)]


async def _build_day(
    retreat: dict, day_no: int, heart_prompt: str, deep_prompt: str, guide: dict, kept: dict, meter: pricing.Meter,
    search_provider: str | None = None,
) -> None:
    """Make one day. The parts are written in the order they're heard, each knowing the
    ones before it: the reflection for the heart first, then the deep dive (which sees
    the reflection and builds on it), then the spoken guidance (tailored to both).
    Recordings start as soon as each script is ready. Finished parts are skipped, so a
    resumed build only redoes what's missing; `kept` holds scripts to reuse unchanged
    (a re-record, or a resume)."""
    state = retreat["days"][str(day_no)]
    day = retreat["plan"]["days"][day_no - 1]
    voices = state["voices"]
    indexes = [i for i in day.get("image_indexes") or [day["image_index"]] if 0 <= i < len(retreat["images"])]
    image = "; ".join(retreat["images"][i]["description"] for i in indexes) or None
    title = retreat["plan"]["title"]
    tailor = retreat.get("build_options", {}).get("tailor_guide", True) if retreat.get("build_options") else True

    def words_for(section: str) -> int:
        return int(tts.max_chars(voices[section]) / 6 * 0.85)  # about six characters per word with spaces

    def ready(group: str, name: str) -> dict | None:
        clip = state[group].get(name) or {}
        return clip if clip.get("status") == "ready" else None

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
    await profile.use_for_job(retreat["user_id"])  # "user info.md" informs every call
    series_text = await series_context(retreat, meter.model)
    recordings: dict[tuple[str, str], asyncio.Task] = {}
    errors: dict[tuple[str, str], Exception] = {}

    def start_recording(group: str, name: str, script: str, voice: str, **extra) -> None:
        if not ready(group, name):
            recordings[(group, name)] = asyncio.create_task(record(group, name, script, voice, **extra))

    async with _jobs:
        # The reading needs no writing: record it right away.
        start_recording("tracks", "reading", re.sub(r"\n{3,}", "\n\n", day["passage_text"]).strip(), voices["reading"])

        # 1. For the heart.
        heart_script = ""
        try:
            llm_log.tag(purpose="heart")
            done = ready("tracks", "heart") or kept.get("heart")
            if done:
                heart_script = done["script"]
            else:
                state["tracks"]["heart"]["status"] = "writing"
                heart_script = await llm.write_heart(prompts.day_context(title, day, image), heart_prompt,
                                                     words_for("heart"), meter, series_text)
            start_recording("tracks", "heart", heart_script, voices["heart"])
        except Exception as exc:
            errors[("tracks", "heart")] = exc

        # 2. The deep dive, knowing the reflection.
        deep_script = ""
        try:
            llm_log.tag(purpose="deep")
            done = ready("tracks", "deep") or kept.get("deep")
            if done:
                deep_script = done["script"]
                start_recording("tracks", "deep", deep_script, voices["deep"], sources=done.get("sources", []),
                                web_search=done.get("web_search"), research=done.get("research"))
            else:
                state["tracks"]["deep"]["status"] = "writing"
                context = prompts.day_context(title, day, image, heart=heart_script)
                deep_script, sources, searched = await llm.write_deep(context, deep_prompt, words_for("deep"), meter,
                                                                      search_provider, series_text)
                # For Jetstream models `searched` names the research service that answered.
                start_recording("tracks", "deep", deep_script, voices["deep"], sources=sources, web_search=bool(searched),
                                research=search.PROVIDERS.get(searched) if isinstance(searched, str) else None)
        except Exception as exc:
            errors[("tracks", "deep")] = exc

        # 3. The spoken guidance, tailored to what the listener will hear.
        lines = {name: prompts.guide_text(template, day) for name, template in guide.items()}
        missing = {n: t for n, t in lines.items() if not ready("guide", n)}
        if missing:
            reuse = kept.get("guide") or {}
            if reuse and all(n in reuse for n in missing):
                texts = {n: reuse[n] for n in missing}
            elif tailor:
                llm_log.tag(purpose="guide")
                texts = await llm.tailor_guide(prompts.day_context(title, day, image), heart_script, deep_script,
                                               missing, meter)
            else:
                texts = missing
            for name, text in texts.items():
                start_recording("guide", name, text, voices["guide"])

        results = await asyncio.gather(*recordings.values(), return_exceptions=True)
    for key, result in zip(recordings, results):
        if isinstance(result, Exception):
            errors[key] = result

    messages = []
    for (group, name), exc in errors.items():
        known = isinstance(exc, (llm.LLMError, tts.TTSError, StorageError))
        if not known:
            log.error("day build failed", exc_info=exc)
        message = str(exc) if known else "Unexpected error."
        state[group].setdefault(name, {}).update(status="failed", error=message)
        messages.append(f"{name}: {message}")
    state["status"] = "failed" if messages else "ready"
    state["error"] = "; ".join(dict.fromkeys(messages)) or None
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
