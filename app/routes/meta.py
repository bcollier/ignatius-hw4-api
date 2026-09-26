"""What the page needs before anything else: health, menus and defaults, and who's signed in."""

import time

from fastapi import APIRouter, Depends

from .. import auth, config, pricing, prompts, search, talk, tts
from ..auth import User, current_user

router = APIRouter()


@router.get("/")
def root():
    return {"name": "Ignatius at Home API", "docs": "/docs", "health": "/api/health"}


@router.get("/api/health")
def health():
    return {
        "ok": True,
        "llm": config.LLM_MODE,
        "model": config.LLM_MODEL if config.LLM_MODE != "stub" else None,
        "tiers": list(tts.tiers()),
        "sign_in": auth.enabled(),
        "server_time": time.time(),
    }


@router.get("/api/options")
async def options():
    """Everything the frontend needs before sign-in: menus, default prompts, and
    the public Supabase settings for the sign-in form."""
    return {
        "tiers": tts.tiers(),
        "prompts": prompts.defaults(),
        "limits": {"max_upload_mb": config.MAX_UPLOAD_MB, "max_pages": config.MAX_PAGES},
        "models": await pricing.model_menu(),
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


@router.get("/api/me")
def me(user: User = Depends(current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "anonymous": user.anonymous,
        "mode": "full" if user.full else "free",
    }
