"""The Costs page: what each retreat cost, by part and by company, and the prices used."""

from fastapi import APIRouter, Depends

from .. import config, costs, demos, pipeline, pricing
from ..auth import User, current_user
from ..storage import store

router = APIRouter()
TALK_USD_PER_MINUTE_OPENAI = 0.05


@router.get("/api/costs")
async def cost_report(user: User = Depends(current_user)):
    """What each of your retreats cost, by part and by company, and the prices used."""
    retreats = [r for r in [await pipeline.get(x["id"]) for x in await store.list_for(user.id)] if r]
    registered = await demos.registry()
    report = costs.report(retreats, await store.usage_rows(user.id))
    for r in report["retreats"]:
        r["example"] = r["id"] in registered  # examples built under this account are listed too
    report["prices"] = await _prices(user)
    return report


async def _prices(user: User) -> dict:
    return {
        "models": [m for m in await pricing.model_menu() if not m.get("free")],
        "elevenlabs_usd_per_1k_chars": config.ELEVENLABS_USD_PER_1K_CHARS,
        "elevenlabs_balance": await pricing.elevenlabs_balance() if config.ELEVENLABS_API_KEY and user.full else None,
        "talk_usd_per_minute": {"openai": TALK_USD_PER_MINUTE_OPENAI, "xai": config.XAI_USD_PER_MINUTE},
    }
