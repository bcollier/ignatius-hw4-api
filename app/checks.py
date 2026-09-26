"""Checking what a request asks for before any work starts: prompts, dates, models,
series, and the build options shared by "make a retreat" and "rebuild a day".
Each check either returns a clean value or raises an HTTP error the page can show."""

from datetime import date

from fastapi import HTTPException
from pydantic import BaseModel

from . import config, pipeline, pricing, prompts, search, series, tts
from .auth import User

DEFAULT_VOICE = "en-US-AndrewMultilingualNeural"
MAX_TITLE_CHARS = 200


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


def check_title(raw: str) -> str:
    title = raw.strip()
    if not 1 <= len(title) <= MAX_TITLE_CHARS:
        raise HTTPException(400, f"The title must be 1 to {MAX_TITLE_CHARS} characters.")
    return title


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


def check_model(model: str | None, user: User) -> str:
    """Free accounts use the Jetstream models; full accounts any listed model."""
    free_models = [m for m, _ in pricing.jetstream_models()]
    if not user.full:
        model = model or (free_models[0] if free_models else "")
        if model not in free_models:
            raise HTTPException(403, "Free mode uses the free open models. Claude is reserved for the site owner.")
        return model
    model = model or config.LLM_MODEL
    if model not in pricing.model_ids():
        raise HTTPException(400, f"Unknown model: {model}")
    return model


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
    # Web research: a key of search_providers, or "none".
    search_provider: str | None = None
    # Adapt the spoken guidance to the day's reflection and deep dive.
    tailor_guide: bool = True


def resolve_build(body: BuildRequest, user: User) -> dict:
    """Check build options and fill in defaults. Used when making a whole retreat and
    when rebuilding one day, so both follow the same rules (free mode included)."""
    # Checked in this order, so when several things are wrong the same one is reported first.
    heart = check_prompt(body.heart_prompt, prompts.HEART_PRESETS["companion"], "heart")
    deep = check_prompt(body.deep_prompt, prompts.DEEP_INSTRUCTIONS, "deep dive")
    model = check_model(body.model, user)
    voices = _check_voices(body, user)
    guide = _check_guide(body.guide)
    provider = _check_search_provider(body.search_provider)
    return {"voices": voices, "heart_prompt": heart, "deep_prompt": deep, "guide": guide,
            "write_model": model, "search_provider": provider, "tailor_guide": body.tailor_guide}


def _check_voices(body: BuildRequest, user: User) -> dict[str, str]:
    voices = {section: body.voices.get(section) or body.voice for section in pipeline.SECTIONS}
    # The free-voice rule comes first, so a free account asking for a premium voice
    # hears "free mode" rather than "unknown voice".
    if not user.full and any(v not in tts.FREE_VOICES for v in voices.values()):
        raise HTTPException(403, "Free mode uses the free Microsoft voices.")
    for voice in voices.values():
        try:
            tts.tier_of(voice)
        except tts.TTSError as exc:
            raise HTTPException(400, str(exc)) from exc
    return voices


def _check_guide(requested: dict[str, str]) -> dict[str, str]:
    """The spoken guidance lines: defaults filled in, empty lines left out."""
    guide = {}
    for name, default in prompts.GUIDE_DEFAULTS.items():
        text = requested.get(name, default).strip()
        if len(text) > prompts.MAX_GUIDE_CHARS:
            raise HTTPException(400, f"The '{name}' guidance is longer than {prompts.MAX_GUIDE_CHARS} characters.")
        if text:
            guide[name] = text
    return guide


def _check_search_provider(requested: str | None) -> str | None:
    provider = requested or search.default_provider()  # None when no service has a key
    if provider == "none":
        return None
    if provider is not None and provider not in search.configured():
        raise HTTPException(400, f"Unknown or unavailable search service: {provider}")
    return provider
