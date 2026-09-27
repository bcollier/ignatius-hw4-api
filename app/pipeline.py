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

from . import config, inspiration, llm, llm_log, pricing, profile, prompts, search, series, tts
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

    def show(text: str) -> None:  # the page reads the retreat from memory while the job runs
        retreat["activity"] = {"text": text, "at": time.time()}

    llm_log.activity_hook.set(show)
    beater = asyncio.create_task(beat())
    try:
        await coro
    finally:
        beater.cancel()
        retreat.pop("activity", None)


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
        return await _give_up(retreat)

    log.warning("resuming interrupted job for retreat %s (attempt %s)", retreat["id"], retreat["resumes"])
    llm_log.tag(email=retreat.get("owner_email"))
    if retreat["status"] == "planning" and retreat.get("composing"):
        retreat.update(status="failed", error="Choosing the passages was interrupted by a server restart. Please try again.")
        return await save(retreat)
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
    await _resume_day_builds(retreat)


async def _give_up(retreat: dict) -> None:
    """Interrupted too many times: mark what was under way failed rather than loop."""
    if retreat["status"] == "planning":
        retreat.update(status="failed", error="Planning was interrupted too many times. Upload the document again.")
    for state in retreat["days"].values():
        if state["status"] in ("building", "queued"):
            state.update(status="failed", error="Making this day was interrupted too many times. Try again.")
    if retreat["status"] == "building":
        _finish_building(retreat)
    await save(retreat)


async def _resume_day_builds(retreat: dict) -> None:
    """Single days being rebuilt when the server went away."""
    for day_no, state in retreat["days"].items():
        if state["status"] != "building":
            continue
        p = state.get("params")
        if not p:  # built before resuming existed
            state.update(status="failed", error="The build was interrupted by a server restart. Build this day again.")
            await save(retreat)
            continue
        meter = pricing.Meter(p["model"], await pricing.prices())
        spawn(retreat, _build_day(retreat, int(day_no), p["heart_prompt"], p["deep_prompt"], p["guide"],
                                  _kept_scripts(state), meter, p.get("search_provider")))


def _kept_scripts(state: dict) -> dict:
    """The reflection and deep dive already written, so a resumed day doesn't write them again."""
    return {k: t for k, t in state["tracks"].items() if t.get("script") and k in ("heart", "deep")}


async def _save_research(retreat: dict, day_no: int, day: dict, meter, cited: list[str]) -> str | None:
    """Keep what the deep dive's research found (queries, every result, which were cited)
    for the research page. Never fails the build."""
    if not meter.research:
        return None
    path = _source_path(retreat, f"day{day_no}_research.json")
    record = {**meter.research, "cited": cited, "model": meter.model, "made_at": time.time()}
    try:
        await store.put_file(path, json.dumps(record).encode(), "application/json")
        return path
    except Exception:
        log.exception("could not save research for day %s", day_no)
        return None


def _source_path(retreat: dict, name: str) -> str:
    return f"{retreat['user_id']}/{retreat['id']}/{name}"


async def _save_source(retreat: dict, source: Extracted) -> None:
    """Keep what planning needs, so an interrupted plan can restart."""
    meta = {"kind": source.kind, "text": source.text, "page_count": source.page_count, "truncated": source.truncated,
            "scanned": [img.page for img in source.scanned_pages],
            "scanned_mimes": [img.mime for img in source.scanned_pages]}
    await store.put_file(_source_path(retreat, "source.json"), json.dumps(meta).encode(), "application/json")
    for i, img in enumerate(source.scanned_pages):
        await store.put_file(_source_path(retreat, f"scan{i}.png"), img.data, img.mime)


async def _load_source(retreat: dict) -> Extracted | None:
    try:
        meta = json.loads(await store.get_file(_source_path(retreat, "source.json")))
        images = [Image(await store.get_file(img["path"]), "image/jpeg", 0, 0, img.get("page")) for img in retreat["images"]]
        mimes = meta.get("scanned_mimes") or ["image/png"] * len(meta.get("scanned", []))  # photos are JPEG
        scans = [Image(await store.get_file(_source_path(retreat, f"scan{i}.png")), mimes[i], 0, 0, page)
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
    compose=None, personal: bool = True,
) -> dict:
    """Store the document's images, save a new retreat record, and start planning it
    in the background. Returns the record at once (status "planning").
    With `compose` (a retreat from an idea, app/inspiration.py), the source document is
    written first, in the background: compose() returns (filename, source)."""
    retreat_id = str(uuid.uuid4())
    images = await _store_images(user_id, retreat_id, source)
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
        "source": _source_summary(source),
        "images": images,
        "plan": None,
        "days": {},
        "composing": bool(compose),
        "personal": personal,  # made with the About me notes, or generic
    }
    if compose:
        await store.save(retreat)
        spawn(retreat, _compose_then_plan(retreat, compose, plan_prompt))
        return retreat
    await _save_source(retreat, source)
    await store.save(retreat)
    spawn(retreat, _plan(retreat, source, plan_prompt))
    return retreat


async def _compose_then_plan(retreat: dict, compose, plan_prompt: str) -> None:
    """A retreat from an idea: choose the passages and fetch their text, then plan as usual."""
    llm_log.tag(user_id=retreat["user_id"], retreat_id=retreat["id"])
    try:
        filename, source = await compose()
    except (inspiration.InspirationError, llm.LLMError) as exc:
        retreat.update(status="failed", error=str(exc), composing=False)
        return await save(retreat)
    except Exception:  # anything else still ends the job, rather than leave it "planning"
        log.exception("choosing passages failed for retreat %s", retreat["id"])
        retreat.update(status="failed", error="Choosing the passages didn't work this time. Please try again.", composing=False)
        return await save(retreat)
    retreat.update(filename=filename, source=_source_summary(source), composing=False,
                   images=await _store_images(retreat["user_id"], retreat["id"], source))
    await _save_source(retreat, source)
    await save(retreat)
    await _plan(retreat, source, plan_prompt)


async def _store_images(user_id: str, retreat_id: str, source: Extracted) -> list[dict]:
    images = []
    for i, img in enumerate(source.images):
        path = f"{user_id}/{retreat_id}/image{i}.jpg"
        await store.put_file(path, img.data, img.mime)
        images.append({"index": i, "path": path, "page": img.page, "description": ""})  # described by the planner
    return images


def _source_summary(source: Extracted) -> dict:
    return {
        "kind": source.kind,
        "pages": source.page_count,
        "characters": len(source.text),
        "images": len(source.images),
        "scanned_pages": len(source.scanned_pages),
        "truncated": source.truncated,
    }


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


async def _use_notes(retreat: dict) -> None:
    """The person's About me notes ("user info.md") inform every call, unless they asked
    for a generic retreat (made without them)."""
    if retreat.get("personal", True):
        await profile.use_for_job(retreat["user_id"])
    else:
        prompts.PERSON.set("")


def _plan_watcher():
    """As the plan streams in, say which day is being planned, and its title."""
    def watch(text: str) -> None:
        days = re.findall(r'"day"\s*:\s*(\d+)', text)
        if not days:
            return llm_log.activity("Deciding how the days will go")
        titles = re.findall(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', text[text.rfind('"day"'):])
        llm_log.activity(f"Planning day {days[-1]}" + (f": {titles[0]}" if titles else ""))
    return watch


async def _plan(retreat: dict, source: Extracted, plan_prompt: str) -> None:
    """Plan the retreat, then (when made in one go) make every day."""
    meter = pricing.Meter(retreat["model"], await pricing.prices())
    llm_log.tag(user_id=retreat["user_id"], retreat_id=retreat["id"], purpose="plan")
    await _use_notes(retreat)
    series_text = await series_context(retreat, retreat["model"])
    await _log_planning_start(retreat)
    llm_log.activity("Reading your document")
    watching = llm.stream_watch.set(_plan_watcher())
    async with _jobs:
        try:
            plan = await llm.plan_retreat(source, retreat["filename"], plan_prompt, meter, series_text)
        except llm.LLMError as exc:
            log.warning("planning %s failed: %s", retreat["id"], exc)
            retreat.update(status="failed", error=str(exc), costs={"plan": meter.summary()})
            await llm_log.step(f"Planning failed: {exc}")
            return await save(retreat)
        except Exception:
            log.exception("planning failed")
            retreat.update(status="failed", error="Something went wrong while planning the retreat.")
            return await save(retreat)
        finally:
            llm.stream_watch.reset(watching)  # the days' writing isn't planning
    await _apply_plan(retreat, plan, meter)
    if retreat.get("build_options"):
        await llm_log.step("Next: making each day in order. For each: the reflection for the heart, then web research "
                           "and the deep dive (which knows the reflection), then the spoken guidance tailored to both, "
                           "and every part recorded as soon as its script is ready.")
        retreat["status"] = "building"
        retreat["progress"] = {"done": 0, "total": len(plan["days"]), "current_day": None, "failed": []}
        await save(retreat)
        await _build_all(retreat)
    else:
        retreat["status"] = "ready"
        await save(retreat)


async def _log_planning_start(retreat: dict) -> None:
    """The first two steps of the build log: what was read, and what planning will do."""
    src = retreat["source"]
    pages = f"{src['pages']} page(s), " if src["pages"] else ""
    scanned = f", {src['scanned_pages']} scanned page(s)" if src["scanned_pages"] else ""
    shortened = " (Long document: the text was shortened to fit.)" if src["truncated"] else ""
    await llm_log.step(f"Read {retreat['filename']}: {pages}{src['characters']:,} characters of text, "
                       f"{src['images']} image(s){scanned}.{shortened}")
    looks = " and looks at its images" if src["images"] else ""
    series_note = f" It also reads the {len(retreat['series'])} earlier week(s) of the series." if retreat.get("series") else ""
    await llm_log.step(
        f"Next: planning the retreat with {retreat['model']}. The model reads the whole document{looks}, then "
        "decides the days, each day's passage (copied word for word), a grace to ask for, a focus, and which image "
        f"goes with which day.{series_note}")


async def _apply_plan(retreat: dict, plan: dict, meter: pricing.Meter) -> None:
    """Keep the plan, the image descriptions it wrote, and an empty state for each day."""
    for note in plan.get("images", []):
        if 0 <= note.get("index", -1) < len(retreat["images"]):
            retreat["images"][note["index"]]["description"] = note["description"]
    retreat["plan"] = plan
    first = "queued" if retreat.get("build_options") else "idle"
    retreat["days"] = {
        str(d["day"]): {"status": first, "error": None, "tracks": {}, "guide": {}, "cost": None} for d in plan["days"]
    }
    retreat["costs"] = {"plan": meter.summary()}
    days = "; ".join(f"{d['day']}. {d['title']} ({d.get('source_ref', '')})" for d in plan["days"])
    await llm_log.step(f"Planned “{plan['title']}”, {len(plan['days'])} days: {days}.")


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
                kept = _kept_scripts(state)
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
    failed = retreat["progress"]["failed"]
    await llm_log.step("All days made. The retreat is ready to pray." if not failed
                       else f"Finished, but day(s) {', '.join(map(str, failed))} failed; use Try again on them.")


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


def _carry_cost(meter: pricing.Meter, state: dict) -> None:
    """Start a retry's meter from what the day already spent, so its cost stays whole."""
    before = (state.get("cost") or {}).get("llm") or {}
    meter.input_tokens += before.get("input_tokens", 0)
    meter.output_tokens += before.get("output_tokens", 0)
    meter.searches += before.get("web_searches", 0)
    meter.usd += before.get("usd", 0.0)


async def _log_voice(tier: str, voice: str, part: str, script: str, result: dict | None, ms: int, error: str | None = None) -> None:
    """Each recording is logged like a model call (what was read, by which voice, the result)."""
    await llm_log.record(
        provider="elevenlabs" if tier == "premium" else "microsoft", model=voice, system="",
        messages=[{"role": "user", "content": script}],
        response_text=(f"Recorded {part}: {result['seconds']} seconds, {len(script):,} characters." if result else None),
        response_extra={"part": part, "characters": len(script), **(result or {})},
        usage={"usd": pricing.tts_usd(len(script), tier)}, duration_ms=ms, error=error, purpose="voice")


def can_retry(state: dict) -> bool:
    """A failed day whose failed parts all have their scripts can be finished by
    recording just those parts again, with no new writing."""
    if state.get("status") != "failed" or not state.get("params") or not state.get("voices"):
        return False
    clips = list(state.get("tracks", {}).items()) + list(state.get("guide", {}).items())
    return all(c.get("script") for _, c in clips if c.get("status") != "ready")


async def retry_failed(retreat: dict, day_no: int) -> dict:
    """Try again: record only the parts that failed, from their saved scripts."""
    state = retreat["days"][str(day_no)]
    params = state["params"]
    kept: dict = {"guide": {}}
    for name, clip in state.get("tracks", {}).items():
        if clip.get("status") != "ready":
            kept[name] = {k: v for k, v in clip.items() if k not in ("status", "error")}
            clip.update(status="waiting", error=None)
    for name, clip in state.get("guide", {}).items():
        if clip.get("status") != "ready":
            kept["guide"][name] = clip["script"]
            clip.update(status="waiting", error=None)
    state.update(status="building", error=None)
    await save(retreat)
    meter = pricing.Meter(params["model"], await pricing.prices())
    _carry_cost(meter, state)  # the writing was paid for in the first attempt
    spawn(retreat, _build_day(retreat, day_no, params["heart_prompt"], params["deep_prompt"], params["guide"], kept, meter,
                              params.get("search_provider")))
    return state


def is_exercise(day: dict) -> bool:
    """A day the planner marked as an activity rather than a text to pray with."""
    return day.get("kind") == "exercise"


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
    await _DayBuild(retreat, day_no, heart_prompt, deep_prompt, guide, kept, meter, search_provider).run()


CHARS_PER_WORD = 6  # about six characters per word, spaces included
WORD_BUDGET_MARGIN = 0.85  # ask for a bit less than fits, since models run long


class _DayBuild:
    """The work of making one day (see _build_day), with what its steps share: the
    day's state, its voices, the recordings under way and the errors so far."""

    def __init__(self, retreat: dict, day_no: int, heart_prompt: str, deep_prompt: str, guide: dict, kept: dict,
                 meter: pricing.Meter, search_provider: str | None):
        self.retreat, self.day_no, self.kept, self.meter = retreat, day_no, kept, meter
        self.heart_prompt, self.deep_prompt, self.guide, self.search_provider = heart_prompt, deep_prompt, guide, search_provider
        self.state = retreat["days"][str(day_no)]
        self.day = retreat["plan"]["days"][day_no - 1]
        self.voices = self.state["voices"]
        self.title = retreat["plan"]["title"]
        self.image = _image_descriptions(retreat, self.day)
        options = retreat.get("build_options") or {}
        self.tailor = options.get("tailor_guide", True)
        # A build can size its scripts independently of the voices (demos written at
        # ElevenLabs length but recorded with free voices first).
        self.script_cap = options.get("script_chars")
        self.series_text = ""
        self.so_far = ""
        self.recordings: dict[tuple[str, str], asyncio.Task] = {}
        self.errors: dict[tuple[str, str], Exception] = {}

    async def run(self) -> None:
        llm_log.tag(user_id=self.retreat["user_id"], retreat_id=self.retreat["id"], day=self.day_no)
        await _use_notes(self.retreat)
        self.series_text = await series_context(self.retreat, self.meter.model)
        # The rest of this retreat: earlier days as heard, coming days as readings only.
        self.so_far = prompts.retreat_so_far(self.retreat["plan"], self.retreat["days"], self.day_no)
        if is_exercise(self.day):
            return await self._exercise_day()
        async with _jobs:
            self._record_reading()
            heart = await self._write_heart()
            deep = await self._write_deep(heart)
            await self._write_guidance(heart, deep)
            results = await asyncio.gather(*self.recordings.values(), return_exceptions=True)
        for key, result in zip(self.recordings, results):
            if isinstance(result, Exception):
                self.errors[key] = result
        await self._finish()

    # ------------------------------------------------------------ the parts, in order

    def _record_reading(self) -> None:
        """The reading needs no writing: record it right away."""
        reading = re.sub(r"\n{3,}", "\n\n", self.day.get("passage_text") or "").strip()
        if reading:
            self._start_recording("tracks", "reading", reading, self.voices["reading"])
        else:
            self.errors[("tracks", "reading")] = llm.LLMError("The plan gave this day no passage to read.")

    async def _write_heart(self) -> str:
        """1. The reflection for the heart."""
        heart_script = ""
        try:
            llm_log.tag(purpose="heart")
            await llm_log.step(f"Day {self.day_no}: {self.day['title']} ({self.day.get('source_ref', '')}). "
                               f"Writing the reflection for the heart with {self.meter.model}"
                               + (", knowing the earlier days" if self.day_no > 1 else "") + ".")
            llm_log.activity(f"Day {self.day_no}: writing the reflection for the heart")
            done = self._ready("tracks", "heart") or self.kept.get("heart")
            if done:
                heart_script = done["script"]
            else:
                self.state["tracks"]["heart"]["status"] = "writing"
                context = prompts.day_context(self.title, self.day, self.image, so_far=self.so_far)
                heart_script = await llm.write_heart(context, self.heart_prompt, self._words_for("heart"), self.meter,
                                                     self.series_text)
            self._start_recording("tracks", "heart", heart_script, self.voices["heart"])
        except Exception as exc:
            self.errors[("tracks", "heart")] = exc
        return heart_script

    async def _write_deep(self, heart_script: str) -> str:
        """2. The deep dive, knowing the reflection."""
        deep_script = ""
        try:
            llm_log.tag(purpose="deep")
            await llm_log.step(f"Day {self.day_no}: researching and writing the deep dive"
                               + (f" (web research: {self.search_provider})" if self.search_provider else "") + ".")
            llm_log.activity(f"Day {self.day_no}: researching the passage and writing the deep dive")
            done = self._ready("tracks", "deep") or self.kept.get("deep")
            if done:
                deep_script = done["script"]
                self._start_recording("tracks", "deep", deep_script, self.voices["deep"], sources=done.get("sources", []),
                                      web_search=done.get("web_search"), research=done.get("research"),
                                      research_path=done.get("research_path"))
            else:
                deep_script = await self._write_new_deep(heart_script)
        except Exception as exc:
            self.errors[("tracks", "deep")] = exc
        return deep_script

    async def _write_new_deep(self, heart_script: str) -> str:
        self.state["tracks"]["deep"]["status"] = "writing"
        context = prompts.day_context(self.title, self.day, self.image, heart=heart_script, so_far=self.so_far)
        deep_script, sources, searched = await llm.write_deep(context, self.deep_prompt, self._words_for("deep"),
                                                              self.meter, self.search_provider, self.series_text)
        research_path = await _save_research(self.retreat, self.day_no, self.day, self.meter, sources)
        self._start_recording("tracks", "deep", deep_script, self.voices["deep"], sources=sources,
                              web_search=bool(searched), research=_research_label(searched), research_path=research_path)
        return deep_script

    async def _write_guidance(self, heart_script: str, deep_script: str) -> None:
        """3. The spoken guidance, tailored to what the listener will hear."""
        lines = {name: prompts.guide_text(template, self.day) for name, template in self.guide.items()}
        missing = {n: t for n, t in lines.items() if not self._ready("guide", n)}
        if not missing:
            return
        reuse = self.kept.get("guide") or {}
        if reuse and all(n in reuse for n in missing):
            texts = {n: reuse[n] for n in missing}
        elif self.tailor:
            llm_log.tag(purpose="guide")
            await llm_log.step(f"Day {self.day_no}: tailoring the spoken guidance to what the listener will hear.")
            texts = await llm.tailor_guide(prompts.day_context(self.title, self.day, self.image), heart_script,
                                           deep_script, missing, self.meter)
        else:
            texts = missing
        for name, text in texts.items():
            self._start_recording("guide", name, text, self.voices["guide"])

    # ------------------------------------------------------------ recording

    def _ready(self, group: str, name: str) -> dict | None:
        clip = self.state[group].get(name) or {}
        return clip if clip.get("status") == "ready" else None

    def _words_for(self, section: str) -> int:
        """How many words to ask the writer for, so the script fits the section's voice."""
        cap = tts.max_chars(self.voices[section])
        if self.script_cap:
            cap = min(cap, self.script_cap)
        return int(cap / CHARS_PER_WORD * WORD_BUDGET_MARGIN)

    def _start_recording(self, group: str, name: str, script: str, voice: str, **extra) -> None:
        if not self._ready(group, name):
            self.recordings[(group, name)] = asyncio.create_task(self._record(group, name, script, voice, **extra))

    async def _record(self, group: str, name: str, script: str, voice: str, **extra) -> None:
        """Record one clip, store it, and keep its word timings for the text on screen."""
        clip = self.state[group][name]
        script, trimmed = fit(script, tts.max_chars(voice))
        clip.update(status="speaking", script=script, characters=len(script), trimmed=trimmed, voice=voice, **extra)
        llm_log.activity(f"Day {self.day_no}: turning {_part_words(group, name)} into voice")
        await save(self.retreat)
        timer = llm_log.Timer()
        tier = tts.tier_of(voice)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "clip.mp3"
            try:
                seconds = await tts.synthesize(script, voice, out)
            except Exception as exc:
                await _log_voice(tier, voice, name, script, None, timer.ms, error=str(exc))
                raise
            words = _word_timings(out)
            path = f"{self.retreat['user_id']}/{self.retreat['id']}/day{self.day_no}_{name}.mp3"
            await store.put_file(path, out.read_bytes(), "audio/mpeg")
        clip.update(status="ready", path=path, seconds=seconds, words=words)
        await _log_voice(tier, voice, name, script, {"seconds": seconds, "path": path, "timed_words": len(words or [])},
                         timer.ms)
        await save(self.retreat)

    # ------------------------------------------------------------ the end of the day

    async def _exercise_day(self) -> None:
        """A day that is an activity from the handout (a worksheet, a review): nothing to
        write or record; the page shows the instruction and a Mark as complete button."""
        self.state.update(kind="exercise", tracks={}, guide={}, status="ready", error=None,
                          cost=_day_cost(self.state, self.meter))
        await llm_log.step(f"Day {self.day_no}: {self.day['title']} is an exercise from the handout, "
                           "so there's nothing to record. Its page shows the instruction and Mark as complete.")
        if self.retreat.get("progress"):
            _count_progress(self.retreat)
        await save(self.retreat)

    async def _finish(self) -> None:
        """Mark failed parts, the day's status and cost, and the retreat's progress."""
        messages = [self._mark_failed(group, name, exc) for (group, name), exc in self.errors.items()]
        summary = "; ".join(dict.fromkeys(messages))
        self.state["status"] = "failed" if messages else "ready"
        await llm_log.step(f"Day {self.day_no} ready." if not messages else f"Day {self.day_no} didn't finish: {summary}")
        self.state["error"] = summary or None
        self.state["cost"] = _day_cost(self.state, self.meter)
        if self.retreat.get("progress"):
            _count_progress(self.retreat)  # a retried day clears itself from the failed list
        await save(self.retreat)

    def _mark_failed(self, group: str, name: str, exc: Exception) -> str:
        # Our own errors carry messages written for the listener; anything else is a bug.
        known = isinstance(exc, (llm.LLMError, tts.TTSError, StorageError))
        if not known:
            log.error("day build failed", exc_info=exc)
        message = str(exc) if known else "Unexpected error."
        self.state[group].setdefault(name, {}).update(status="failed", error=message)
        return f"{name}: {message}"


def _part_words(group: str, name: str) -> str:
    """A recorded part as the page names it."""
    if group == "guide":
        return "the spoken guidance"
    return {"reading": "the reading", "heart": "the reflection for the heart", "deep": "the deep dive"}.get(name, name)


def _image_descriptions(retreat: dict, day: dict) -> str | None:
    """What the day's pictures show, so the writers can refer to them."""
    indexes = [i for i in day.get("image_indexes") or [day["image_index"]] if 0 <= i < len(retreat["images"])]
    return "; ".join(retreat["images"][i]["description"] for i in indexes) or None


def _research_label(searched) -> str | None:
    """The research service(s) that answered, as shown on the page. `searched` is a
    service key when the free search services ran, True/False otherwise."""
    if not isinstance(searched, str):
        return None
    return "several services, combined" if searched == "all" else search.PROVIDERS.get(searched)


def _word_timings(recording: Path) -> list | None:
    """[[seconds, character index], ...] left by tts.synthesize, so the page can follow along word by word."""
    timings = tts.words_path(recording)
    return json.loads(timings.read_text()) if timings.exists() else None


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
