"""Ignatius at Home: turn an uploaded PDF or Word document into a guided audio retreat."""

import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, config, demos, llm_log, pipeline, pricing, profile, prompts, script_pdf, search, series, talk, tts
from .auth import User, current_user
from .extract import ExtractError, extract
from .storage import LocalStore, StorageError, store, summary

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await store.setup()
    yield


app = FastAPI(title="Ignatius at Home API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


# Every error comes back as {"error": {"status": ..., "message": ...}}.
@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    return JSONResponse({"error": {"status": exc.status_code, "message": exc.detail}}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"][1:]) or "request"
    return JSONResponse({"error": {"status": 422, "message": f"Invalid {field}: {first['msg']}"}}, status_code=422)


@app.exception_handler(StorageError)
async def storage_error(_: Request, exc: StorageError):
    return JSONResponse({"error": {"status": 503, "message": str(exc)}}, status_code=503)


def check_prompt(text: str | None, default: str, name: str) -> str:
    """A blank prompt means the default; custom prompts are length-limited."""
    text = (text or "").strip()
    if len(text) > prompts.MAX_PROMPT_CHARS:
        raise HTTPException(400, f"The {name} prompt is longer than {prompts.MAX_PROMPT_CHARS} characters.")
    return text or default


def check_date(raw: str | None) -> str:
    """An ISO date (YYYY-MM-DD); blank means today (UTC)."""
    if not raw or not raw.strip():
        return date.today().isoformat()
    try:
        return date.fromisoformat(raw.strip()).isoformat()
    except ValueError as exc:
        raise HTTPException(400, "Start date must look like 2026-10-05.") from exc


async def check_series(raw: str, user: User) -> list[str]:
    """Earlier retreats for a series: comma-separated ids, all the user's own and planned.
    Returned oldest first, whatever order they were sent in."""
    ids = list(dict.fromkeys(i.strip() for i in raw.split(",") if i.strip()))
    if len(ids) > series.MAX_PREVIOUS:
        raise HTTPException(400, f"A series can include up to {series.MAX_PREVIOUS} earlier retreats.")
    found = []
    for rid in ids:
        r = await pipeline.get(rid)
        if not r or r["user_id"] != user.id:
            raise HTTPException(400, "One of the earlier retreats in the series wasn't found.")
        if not r.get("plan"):
            raise HTTPException(400, f"'{r['filename']}' hasn't finished planning, so it can't be part of a series yet.")
        found.append(r)
    return [r["id"] for r in sorted(found, key=lambda r: r["created_at"])]


async def my_retreat(retreat_id: str, user: User = Depends(current_user)) -> dict:
    retreat = await pipeline.get(retreat_id)
    # Someone else's retreat gets the same answer as a missing one.
    if not retreat or retreat["user_id"] != user.id:
        raise HTTPException(404, "Retreat not found.")
    return retreat


async def readable_retreat(retreat_id: str, user: User = Depends(current_user)) -> dict:
    """The person's own retreat, or a demo retreat seen with their own progress laid
    over it (marked read_only; save changes with save_retreat)."""
    retreat = await pipeline.get(retreat_id)
    if retreat and retreat["user_id"] == user.id:
        return retreat
    meta = (await demos.registry()).get(retreat_id)
    if not retreat or not meta:
        raise HTTPException(404, "Retreat not found.")
    mine = (await demos.load_state(user.id)).get(retreat_id)
    view = demos.personal(retreat, mine, meta)
    view["_viewer"] = user.id
    return view


async def save_retreat(retreat: dict) -> None:
    """Owners save the retreat; for a demo only the person's own progress is kept."""
    if not retreat.get("read_only"):
        await pipeline.save(retreat)
        return
    everything = await demos.load_state(retreat["_viewer"])
    everything[retreat["id"]] = {
        "start_date": retreat.get("start_date"),
        "days": {n: {k: d.get(k) for k in ("prayed_at", "journal", "listening")} for n, d in retreat["days"].items()},
    }
    await demos.save_state(retreat["_viewer"], everything)


async def view_of(retreat: dict) -> dict:
    view = await pipeline.public_view(retreat)
    view.pop("_viewer", None)
    return view


@app.get("/")
def root():
    return {"name": "Ignatius at Home API", "docs": "/docs", "health": "/api/health"}


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "llm": config.LLM_MODE,
        "model": config.LLM_MODEL if config.LLM_MODE != "stub" else None,
        "tiers": list(tts.tiers()),
        "sign_in": auth.enabled(),
        "server_time": time.time(),
    }


def check_model(model: str | None, user: User) -> str:
    free_models = [m for m, _ in pricing.jetstream_models()]
    if not user.full:
        model = model or (free_models[0] if free_models else "")
        if model not in free_models:
            raise HTTPException(403, "Free mode uses the Jetstream models. Claude is reserved for the site owner.")
        return model
    model = model or config.LLM_MODEL
    if model not in pricing.model_ids():
        raise HTTPException(400, f"Unknown model: {model}")
    return model


@app.get("/api/options")
async def options():
    """Everything the frontend needs before sign-in: menus, default prompts, and
    the public Supabase settings for the sign-in form."""
    return {
        "tiers": tts.tiers(),
        "prompts": prompts.defaults(),
        "limits": {"max_upload_mb": config.MAX_UPLOAD_MB, "max_pages": config.MAX_PAGES},
        "models": await model_options(),
        "default_model": config.LLM_MODEL,
        "web_search": config.WEB_SEARCH,
        "elevenlabs": {
            "usd_per_1k_chars": config.ELEVENLABS_USD_PER_1K_CHARS,
            "balance": await pricing.elevenlabs_balance(),
        },
        "talk": {**talk.options(), "xai_voices": await talk.xai_voices() if config.XAI_API_KEY else {}},
        "search_providers": search.configured(),
        "search_status": search.status(),
        "default_search_provider": search.default_provider(),
        "free_mode": {
            "enabled": config.FREE_MODE,
            "models": [m for m, _ in pricing.jetstream_models()],
        },
        "auth": {"url": config.SUPABASE_URL, "publishable_key": config.SUPABASE_PUBLISHABLE_KEY}
        if auth.enabled()
        else None,
    }


async def model_options() -> list[dict]:
    """Each model with its price in dollars per million tokens, for menus and estimates."""
    table = await pricing.prices()
    out = []
    for model, _, label in pricing.MODELS:
        p = table.get(model, {})
        out.append({
            "id": model,
            "label": label,
            "input_per_m": round(p.get("prompt", 0) * 1e6, 3),
            "output_per_m": round(p.get("completion", 0) * 1e6, 3),
            "web_search_each": p.get("web_search", 0.01),
        })
    for model, label in pricing.jetstream_models():
        out.append({"id": model, "label": label, "input_per_m": 0, "output_per_m": 0, "web_search_each": 0, "free": True})
    return out


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "anonymous": user.anonymous,
        "mode": "full" if user.full else "free",
    }


class TalkRequest(BaseModel):
    provider: str | None = None
    voice: str | None = None
    sdp: str | None = None  # the browser's WebRTC offer (OpenAI)
    retreat_id: str | None = None
    local_time: str | None = None  # the browser's local time with its offset, e.g. 2026-09-26T21:30:00-04:00


@app.post("/api/talk/session")
async def talk_session(body: TalkRequest, user: User = Depends(current_user)):
    """Start a live conversation with the companion, with the person's notes and the
    retreat (and which days they've listened to) as its context."""
    retreat = None
    if body.retreat_id:
        retreat = await readable_retreat(body.retreat_id, user)
    p = await profile.load(user.id)
    provider = body.provider or talk.options()["default_provider"]
    try:
        return await talk.start(user, retreat, p["about"], p["companion_notes"], provider or "", body.voice or "", body.sdp,
                                body.local_time)
    except talk.TalkError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


class TalkEnd(BaseModel):
    session_id: str
    seconds: int = 0
    transcript: str = ""


@app.post("/api/talk/end")
async def talk_end(body: TalkEnd, user: User = Depends(current_user)):
    """The browser reports the end of a conversation: counts free minutes, logs the transcript."""
    await talk.end(user, body.session_id, body.seconds, body.transcript)
    return {"ok": True}


@app.get("/api/talk/history")
async def talk_history(user: User = Depends(current_user)):
    """Past conversations (dates, retreat, length, transcript when kept) and the memory summary."""
    return await talk.load_history(user.id)


@app.delete("/api/talk/history")
async def forget_talks(user: User = Depends(current_user)):
    """Forget every past conversation and the memory summary."""
    await talk.clear_history(user.id)
    return {"ok": True}


class ProfileRequest(BaseModel):
    about: str | None = None
    companion_notes: str | None = None


@app.get("/api/profile")
async def read_profile(user: User = Depends(current_user)):
    """What the person has told the app about themselves ("user info.md")."""
    return await profile.load(user.id)


@app.put("/api/profile")
async def write_profile(body: ProfileRequest, user: User = Depends(current_user)):
    llm_log.tag(user_id=user.id, email=user.email or ("guest" if user.anonymous else None))
    return await profile.save(user.id, body.about, body.companion_notes, user.full)


@app.post("/api/profile/upload")
async def upload_profile(file: UploadFile = File(...), user: User = Depends(current_user)):
    """Replace the about-me notes with a text, Markdown, Word or PDF file."""
    data = await file.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {config.MAX_UPLOAD_MB} MB.")
    name = (file.filename or "notes").lower()
    if name.endswith((".txt", ".md", ".markdown")):
        text = data.decode("utf-8", errors="replace")
    else:
        try:
            text = extract(file.filename or "notes", data).text
        except ExtractError as exc:
            raise HTTPException(400, "Upload a text (.txt or .md), Word (.docx) or PDF file.") from exc
    text = text.replace("[Page ", "\n[Page ")
    if not text.strip():
        raise HTTPException(400, "No text found in that file.")
    llm_log.tag(user_id=user.id, email=user.email or ("guest" if user.anonymous else None))
    return await profile.save(user.id, text, None, user.full, source=file.filename or "upload")


@app.get("/api/retreats")
async def list_retreats(user: User = Depends(current_user)):
    mine = await store.list_for(user.id)
    own_ids = {r["id"] for r in mine}
    examples = []
    registered = await demos.registry()
    state = await demos.load_state(user.id) if registered else {}
    for rid, meta in registered.items():
        retreat = None if rid in own_ids else await pipeline.get(rid)
        if retreat and retreat.get("status") == "ready":
            first = next((d.get("image_index", -1) for d in retreat["plan"]["days"] if d.get("image_index", -1) >= 0), -1)
            cover = retreat["images"][first]["path"] if 0 <= first < len(retreat["images"]) else None
            examples.append({**summary(demos.personal(retreat, state.get(rid), meta)), "demo": meta, "read_only": True,
                             "cover": (await store.urls([cover])).get(cover) if cover else None})
    return {"retreats": mine, "examples": examples}


@app.post("/api/retreats", status_code=202)
async def create_retreat(
    file: UploadFile = File(...),
    plan_prompt: str = Form(""),
    model: str = Form(""),
    series_ids: str = Form("", alias="series"),
    options: str = Form(""),
    start_date: str = Form(""),
    user: User = Depends(current_user),
):
    """Upload and make a retreat. With `options` (the build settings as JSON, the same
    fields as a day build), every day is made right after planning: one request, and
    the retreat comes back ready to pray. Without it, only the plan is made."""
    plan_prompt = check_prompt(plan_prompt, prompts.PLAN_INSTRUCTIONS, "planning")
    model = check_model(model, user)
    build_options = None
    if options.strip():
        try:
            body = BuildRequest.model_validate_json(options)
        except ValueError as exc:
            raise HTTPException(400, "The build options couldn't be read.") from exc
        build_options = resolve_build(body, user)  # fails here, before the upload is processed
    start = check_date(start_date)
    data = await file.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {config.MAX_UPLOAD_MB} MB.")
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    try:
        source = extract(file.filename or "upload", data)
    except ExtractError as exc:
        raise HTTPException(400, str(exc)) from exc
    llm_log.tag(email=user.email or ("guest" if user.anonymous else None))  # inherited by the planning job
    ids = await check_series(series_ids, user)
    retreat = await pipeline.create_retreat(
        user.id, file.filename or "upload", source, plan_prompt, model,
        email=user.email or ("guest" if user.anonymous else None), series_ids=ids,
        build_options=build_options, start_date=start,
    )
    return await pipeline.public_view(retreat)


@app.get("/api/retreats/{retreat_id}")
async def read_retreat(retreat: dict = Depends(readable_retreat)):
    return await view_of(retreat)


@app.get("/api/retreats/{retreat_id}/research")
async def research(retreat: dict = Depends(readable_retreat)):
    """Research done for this retreat: for each day, the passage and notes it was made
    from, the searches run for the deep dive, every result, and which were cited."""
    days = []
    for d in (retreat.get("plan") or {}).get("days", []):
        state = retreat["days"].get(str(d["day"]), {})
        deep = state.get("tracks", {}).get("deep", {})
        found = None
        if deep.get("research_path"):
            try:
                found = json.loads(await store.get_file(deep["research_path"]))
            except (StorageError, ValueError):
                found = None
        days.append({
            "day": d["day"], "title": d.get("title"), "source_ref": d.get("source_ref"),
            "passage_text": d.get("passage_text"), "notes": {k: d.get(k) for k in ("theme", "grace", "image_description") if d.get(k)},
            "status": state.get("status"), "web_search": deep.get("web_search"), "research_service": deep.get("research"),
            "cited": deep.get("sources", []), "research": found,
        })
    return {"id": retreat["id"], "title": (retreat.get("plan") or {}).get("title"), "model": retreat.get("model"),
            "source_filename": retreat.get("filename"), "days": days}


@app.get("/api/retreats/{retreat_id}/script.pdf")
async def script(
    retreat: dict = Depends(readable_retreat),
    day: int | None = None,
    order: str = "lectio",
    grace_silence: int = 15,
    pause: int = 30,
):
    """The printable script: one day (?day=n) or the whole retreat, in the same order
    and with the same silences the player uses."""
    if retreat["status"] != "ready":
        raise HTTPException(409, "The retreat plan isn't ready yet.")
    if order not in ("lectio", "simple"):
        raise HTTPException(400, "order must be 'lectio' or 'simple'.")
    days = retreat["plan"]["days"]
    if day is not None:
        days = [d for d in days if d["day"] == day]
        if not days:
            raise HTTPException(404, f"This retreat has no day {day}.")
    images: dict[str, bytes] = {}
    titles = []
    for rid in retreat.get("series", []) if day is None else []:
        earlier = await pipeline.get(rid)
        if earlier and earlier["user_id"] == retreat["user_id"] and earlier.get("plan"):
            titles.append(earlier["plan"]["title"])
    sections = [] if day is not None else [script_pdf.cover_html(retreat, days, titles)]
    for d in days:
        name = None
        if d["image_index"] >= 0:
            name = f"image{d['image_index']}.jpg"
            if name not in images:
                images[name] = await store.get_file(retreat["images"][d["image_index"]]["path"])
        state = retreat["days"].get(str(d["day"]))
        sections.append(script_pdf.day_html(retreat, d, state, order, grace_silence, pause, name))
    pdf = script_pdf.render(sections, images)
    slug = "".join(c if c.isalnum() else "-" for c in retreat["plan"]["title"].lower()).strip("-")[:60] or "retreat"
    filename = f"{slug}-day-{day}.pdf" if day is not None else f"{slug}.pdf"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{filename}"'})


class RetreatPatch(BaseModel):
    start_date: str | None = None
    title: str | None = None


@app.patch("/api/retreats/{retreat_id}")
async def update_retreat(body: RetreatPatch, retreat: dict = Depends(readable_retreat)):
    """Change the start date (which day is 'today') or the title."""
    if body.start_date is not None:
        retreat["start_date"] = check_date(body.start_date)
    if body.title is not None and retreat.get("read_only"):
        raise HTTPException(403, "Example retreats can't be renamed.")
    if body.title is not None and retreat.get("plan"):
        title = body.title.strip()
        if not 1 <= len(title) <= 200:
            raise HTTPException(400, "The title must be 1 to 200 characters.")
        retreat["plan"]["title"] = title
    await save_retreat(retreat)
    return await view_of(retreat)


def _day_state(retreat: dict, day: int) -> dict:
    state = retreat["days"].get(str(day))
    if state is None:
        raise HTTPException(404, f"This retreat has no day {day}.")
    return state


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PrayedRequest(BaseModel):
    prayed: bool = True
    word: str | None = None
    note: str | None = None


@app.post("/api/retreats/{retreat_id}/days/{day}/prayed")
async def mark_prayed(day: int, body: PrayedRequest, retreat: dict = Depends(readable_retreat)):
    """Mark a day prayed (or not), and keep the word that stayed and a short note."""
    state = _day_state(retreat, day)
    word, note = (body.word or "").strip(), (body.note or "").strip()
    if len(word) > 100:
        raise HTTPException(400, "The word or phrase can be up to 100 characters.")
    if len(note) > 2000:
        raise HTTPException(400, "The note can be up to 2,000 characters.")
    state["prayed_at"] = (state.get("prayed_at") or _now()) if body.prayed else None
    if word or note:
        state["journal"] = {"word": word, "note": note, "at": _now()}
    elif body.word is not None or body.note is not None:
        state["journal"] = None  # both cleared
    await save_retreat(retreat)
    return await view_of(retreat)


class ProgressRequest(BaseModel):
    step: int = 0
    part: str = ""
    seconds: float = 0
    parts_played: list[str] = []
    finished: bool = False


@app.post("/api/retreats/{retreat_id}/days/{day}/progress")
async def listening_progress(day: int, body: ProgressRequest, retreat: dict = Depends(readable_retreat)):
    """What has been played of a day, so a missed or interrupted day is known later and
    can be continued on any device. Finishing the prayer marks the day prayed."""
    state = _day_state(retreat, day)
    now = _now()
    listening = state.get("listening") or {"parts_played": [], "started_at": now}
    played = list(dict.fromkeys(listening["parts_played"] + [p[:40] for p in body.parts_played][:50]))
    listening.update(parts_played=played, last_step=max(0, body.step), last_part=body.part[:60],
                     seconds_in_part=max(0.0, body.seconds), updated_at=now)
    if body.finished:
        listening["finished_at"] = now
        state["prayed_at"] = state.get("prayed_at") or now
    state["listening"] = listening
    await save_retreat(retreat)
    return {"listening": listening, "prayed_at": state.get("prayed_at")}


@app.delete("/api/retreats/{retreat_id}")
async def delete_retreat(retreat: dict = Depends(my_retreat)):
    if retreat["id"] in pipeline.active:
        raise HTTPException(409, "Wait for the current job to finish before deleting this retreat.")
    await store.delete(retreat)
    return {"deleted": retreat["id"]}


DEFAULT_VOICE = "en-US-AndrewMultilingualNeural"


class BuildRequest(BaseModel):
    # A voice id for each section: guide, reading, heart, deep. Missing sections
    # use `voice`. Voice ids from /api/options; the tier follows from the id.
    voices: dict[str, str] = {}
    voice: str = DEFAULT_VOICE
    heart_prompt: str | None = None
    deep_prompt: str | None = None
    # Spoken guidance by name (opening, first, second, third, silence, last, closing);
    # missing names use the defaults. An empty string leaves that clip out.
    guide: dict[str, str] = {}
    # Re-record with new voices but keep the written reflection and deep dive.
    keep_scripts: bool = False
    # Which model writes the reflection and deep dive (an id from /api/options).
    model: str | None = None
    # Web research for Jetstream models: a key of search_providers, or "none".
    search_provider: str | None = None
    # Adapt the spoken guidance to the day's reflection and deep dive.
    tailor_guide: bool = True


def resolve_build(body: BuildRequest, user: User) -> dict:
    """Check build options and fill in defaults. Used when making a whole retreat and
    when rebuilding one day, so both follow the same rules (free mode included)."""
    heart = check_prompt(body.heart_prompt, prompts.HEART_PRESETS["companion"], "heart")
    deep = check_prompt(body.deep_prompt, prompts.DEEP_INSTRUCTIONS, "deep dive")
    model = check_model(body.model, user)
    voices = {section: body.voices.get(section) or body.voice for section in pipeline.SECTIONS}
    if not user.full and any(v not in tts.FREE_VOICES for v in voices.values()):
        raise HTTPException(403, "Free mode uses the free Microsoft voices.")
    for voice in voices.values():
        try:
            tts.tier_of(voice)
        except tts.TTSError as exc:
            raise HTTPException(400, str(exc)) from exc
    guide = {}
    for name, default in prompts.GUIDE_DEFAULTS.items():
        text = body.guide.get(name, default).strip()
        if len(text) > prompts.MAX_GUIDE_CHARS:
            raise HTTPException(400, f"The '{name}' guidance is longer than {prompts.MAX_GUIDE_CHARS} characters.")
        if text:
            guide[name] = text
    provider = body.search_provider or search.default_provider()  # None when no service has a key
    if provider == "none":
        provider = None
    elif provider is not None and provider not in search.configured():
        raise HTTPException(400, f"Unknown or unavailable search service: {provider}")
    return {"voices": voices, "heart_prompt": heart, "deep_prompt": deep, "guide": guide,
            "write_model": model, "search_provider": provider, "tailor_guide": body.tailor_guide}


@app.post("/api/retreats/{retreat_id}/days/{day}/build", status_code=202)
async def build_day(
    day: int, body: BuildRequest, retreat: dict = Depends(my_retreat), user: User = Depends(current_user)
):
    """Rebuild one day: Rewrite, Re-record (keep_scripts) or Try again."""
    if retreat["status"] == "building":
        raise HTTPException(409, "This retreat is still being made. Wait for it to finish.")
    if retreat["status"] != "ready":
        raise HTTPException(409, "The retreat plan isn't ready yet.")
    state = retreat["days"].get(str(day))
    if state is None:
        raise HTTPException(404, f"This retreat has no day {day}.")
    if state["status"] in ("building", "queued"):
        raise HTTPException(409, f"Day {day} is already being made.")
    opts = resolve_build(body, user)
    llm_log.tag(email=user.email or ("guest" if user.anonymous else None))  # inherited by the build job
    try:
        await pipeline.start_day_build(retreat, day, opts["voices"], opts["heart_prompt"], opts["deep_prompt"],
                                       opts["guide"], body.keep_scripts, opts["write_model"], opts["search_provider"])
    except tts.TTSError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await pipeline.public_view(retreat)


@app.post("/api/retreats/{retreat_id}/days/{day}/retry", status_code=202)
async def retry_day(day: int, retreat: dict = Depends(my_retreat), user: User = Depends(current_user)):
    """Try again after a failure: record only the parts that failed, from their saved
    scripts, with the day's own options. Nothing is written again."""
    state = _day_state(retreat, day)
    if not pipeline.can_retry(state):
        raise HTTPException(409, f"Day {day} can't be finished from its scripts; rewrite it instead.")
    llm_log.tag(email=user.email or ("guest" if user.anonymous else None))
    await pipeline.retry_failed(retreat, day)
    return await pipeline.public_view(retreat)


if isinstance(store, LocalStore):
    # Local development only. With Supabase, files are served by signed Storage URLs.
    @app.get("/api/files/{path:path}")
    def read_file(path: str):
        try:
            target = store.local_path(path)
        except StorageError:
            raise HTTPException(404, "File not found.")
        if not target.is_file():
            raise HTTPException(404, "File not found.")
        media = "audio/mpeg" if target.suffix == ".mp3" else "image/jpeg"
        return FileResponse(target, media_type=media)
