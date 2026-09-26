"""Ignatius at Home: turn an uploaded PDF or Word document into a guided audio retreat."""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, config, llm_log, pipeline, pricing, prompts, script_pdf, search, tts
from .auth import User, current_user
from .extract import ExtractError, extract
from .storage import LocalStore, StorageError, store

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await store.setup()
    yield


app = FastAPI(title="Ignatius at Home API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
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


async def my_retreat(retreat_id: str, user: User = Depends(current_user)) -> dict:
    retreat = await pipeline.get(retreat_id)
    # Someone else's retreat gets the same answer as a missing one.
    if not retreat or retreat["user_id"] != user.id:
        raise HTTPException(404, "Retreat not found.")
    return retreat


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
        "search_providers": search.configured(),
        "search_status": search.status(),
        "default_search_provider": search.default_provider(),
        "free_mode": {
            "enabled": config.FREE_MODE,
            "models": [m for m, _ in pricing.jetstream_models()],
            "max_retreats": config.FREE_MAX_RETREATS,
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
        "max_retreats": None if user.full else config.FREE_MAX_RETREATS,
    }


@app.get("/api/retreats")
async def list_retreats(user: User = Depends(current_user)):
    return {"retreats": await store.list_for(user.id)}


@app.post("/api/retreats", status_code=202)
async def create_retreat(
    file: UploadFile = File(...),
    plan_prompt: str = Form(""),
    model: str = Form(""),
    user: User = Depends(current_user),
):
    plan_prompt = check_prompt(plan_prompt, prompts.PLAN_INSTRUCTIONS, "planning")
    model = check_model(model, user)
    if not user.full and len(await store.list_for(user.id)) >= config.FREE_MAX_RETREATS:
        raise HTTPException(
            403, f"Free mode keeps up to {config.FREE_MAX_RETREATS} retreats. Delete one to make another."
        )
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
    retreat = await pipeline.create_retreat(
        user.id, file.filename or "upload", source, plan_prompt, model, email=user.email or ("guest" if user.anonymous else None)
    )
    return await pipeline.public_view(retreat)


@app.get("/api/retreats/{retreat_id}")
async def read_retreat(retreat: dict = Depends(my_retreat)):
    return await pipeline.public_view(retreat)


@app.get("/api/retreats/{retreat_id}/script.pdf")
async def script(
    retreat: dict = Depends(my_retreat),
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
    sections = [] if day is not None else [script_pdf.cover_html(retreat, days)]
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


@app.post("/api/retreats/{retreat_id}/days/{day}/build", status_code=202)
async def build_day(
    day: int, body: BuildRequest, retreat: dict = Depends(my_retreat), user: User = Depends(current_user)
):
    if retreat["status"] != "ready":
        raise HTTPException(409, "The retreat plan isn't ready yet.")
    state = retreat["days"].get(str(day))
    if state is None:
        raise HTTPException(404, f"This retreat has no day {day}.")
    if state["status"] == "building":
        raise HTTPException(409, f"Day {day} is already being built.")
    heart = check_prompt(body.heart_prompt, prompts.HEART_PRESETS["companion"], "heart")
    deep = check_prompt(body.deep_prompt, prompts.DEEP_INSTRUCTIONS, "deep dive")
    model = check_model(body.model, user)
    voices = {section: body.voices.get(section) or body.voice for section in pipeline.SECTIONS}
    if not user.full and any(v not in tts.FREE_VOICES for v in voices.values()):
        raise HTTPException(403, "Free mode uses the free Microsoft voices.")
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
    llm_log.tag(email=user.email or ("guest" if user.anonymous else None))  # inherited by the build job
    try:
        await pipeline.start_day_build(retreat, day, voices, heart, deep, guide, body.keep_scripts, model, provider)
    except tts.TTSError as exc:
        raise HTTPException(400, str(exc)) from exc
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
