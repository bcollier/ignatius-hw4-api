"""Models the user can choose, their prices, and what each call actually cost.

Prices come from OpenRouter's public model list (refreshed every few hours), with
the table below as a fallback. Costs are computed from each response's token usage.
ElevenLabs bills by character; the dollar rate depends on the plan, so it is an
estimate set by ELEVENLABS_USD_PER_1K_CHARS.
"""

import logging
import time

import httpx

from . import config

log = logging.getLogger(__name__)

# (OpenRouter id, Anthropic API id, label). Older models use the basic web search tool.
MODELS = [
    ("anthropic/claude-opus-5", "claude-opus-5", "Claude Opus 5"),
    ("anthropic/claude-opus-5.5", "claude-opus-5-5", "Claude Opus 5.5"),
    ("anthropic/claude-fable-5.1", "claude-fable-5-1", "Claude Fable 5.1 (most capable)"),
    ("anthropic/claude-sonnet-5", "claude-sonnet-5", "Claude Sonnet 5 (faster, cheaper)"),
    ("anthropic/claude-haiku-4.5", "claude-haiku-4-5", "Claude Haiku 4.5 (fastest, cheapest)"),
]
BASIC_SEARCH_MODELS = {"anthropic/claude-haiku-4.5"}

# Dollars per token, and per web search. Used when OpenRouter can't be reached.
FALLBACK_PRICES = {
    "anthropic/claude-opus-5": {"prompt": 5e-6, "completion": 25e-6, "input_cache_read": 0.5e-6, "input_cache_write": 6.25e-6, "web_search": 0.01},
    "anthropic/claude-opus-5.5": {"prompt": 4e-6, "completion": 20e-6, "input_cache_read": 0.2e-6, "input_cache_write": 5e-6, "web_search": 0.01},
    "anthropic/claude-fable-5.1": {"prompt": 10e-6, "completion": 50e-6, "input_cache_read": 0.25e-6, "input_cache_write": 12.5e-6, "web_search": 0.01},
    "anthropic/claude-sonnet-5": {"prompt": 2e-6, "completion": 10e-6, "input_cache_read": 0.2e-6, "input_cache_write": 2.5e-6, "web_search": 0.01},
    "anthropic/claude-haiku-4.5": {"prompt": 1e-6, "completion": 5e-6, "input_cache_read": 0.1e-6, "input_cache_write": 1.25e-6, "web_search": 0.01},
}

_prices: dict = {}
_prices_at = 0.0
_eleven: dict = {}
_eleven_at = 0.0


def model_ids() -> list[str]:
    return [m[0] for m in MODELS]


def api_model(model: str) -> str:
    """The id to send: OpenRouter's, or the Anthropic API's when calling Anthropic directly."""
    if config.LLM_MODE == "openrouter":
        return model
    return next((a for o, a, _ in MODELS if o == model), model)


def web_search_tool(model: str) -> dict:
    kind = "web_search_20250305" if model in BASIC_SEARCH_MODELS else "web_search_20260209"
    return {"type": kind, "name": "web_search", "max_uses": 5}


async def prices() -> dict:
    global _prices, _prices_at
    if config.LLM_MODE == "stub":
        return dict(FALLBACK_PRICES)
    if _prices and time.time() - _prices_at < 6 * 3600:
        return _prices
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            data = (await http.get("https://openrouter.ai/api/v1/models")).json()["data"]
        live = {m["id"]: {k: float(v) for k, v in m["pricing"].items() if _is_number(v)} for m in data if m["id"] in model_ids()}
        _prices = {**FALLBACK_PRICES, **live}
        _prices_at = time.time()
    except Exception:
        log.warning("couldn't fetch OpenRouter prices; using the built-in table")
        _prices = _prices or dict(FALLBACK_PRICES)
    return _prices


def _is_number(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


class Meter:
    """Adds up what the model calls for one job cost."""

    def __init__(self, model: str, table: dict):
        self.model = model
        self.price = table.get(model) or FALLBACK_PRICES.get(model, {})
        self.input_tokens = self.output_tokens = self.searches = 0
        self.usd = 0.0

    def add(self, usage) -> None:
        if usage is None:
            return
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        server = getattr(usage, "server_tool_use", None)
        searches = (getattr(server, "web_search_requests", 0) or 0) if server else 0
        p = self.price
        self.input_tokens += usage.input_tokens + cache_read + cache_write
        self.output_tokens += usage.output_tokens
        self.searches += searches
        self.usd += (
            usage.input_tokens * p.get("prompt", 0)
            + usage.output_tokens * p.get("completion", 0)
            + cache_read * p.get("input_cache_read", p.get("prompt", 0))
            + cache_write * p.get("input_cache_write", p.get("prompt", 0))
            + searches * p.get("web_search", 0.01)
        )

    def summary(self) -> dict:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "web_searches": self.searches,
            "usd": round(self.usd, 4),
        }


def tts_usd(characters: int, tier: str) -> float:
    return round(characters / 1000 * config.ELEVENLABS_USD_PER_1K_CHARS, 4) if tier == "premium" else 0.0


async def elevenlabs_balance() -> dict | None:
    """Characters used and allowed this billing period, from the ElevenLabs account."""
    global _eleven, _eleven_at
    if not config.ELEVENLABS_API_KEY:
        return None
    if _eleven and time.time() - _eleven_at < 300:
        return _eleven
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(
                "https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": config.ELEVENLABS_API_KEY}
            )
        d = r.json()
        _eleven = {
            "tier": d.get("tier"),
            "used": d.get("character_count"),
            "limit": d.get("character_limit"),
            "remaining": max(0, (d.get("character_limit") or 0) - (d.get("character_count") or 0)),
        }
        _eleven_at = time.time()
    except Exception:
        log.warning("couldn't read the ElevenLabs balance")
        return _eleven or None
    return _eleven
