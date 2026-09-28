"""A log row for every model call: who, what for, the prompt, the response, tokens,
cost and timing. Saved to the llm_calls table (or a JSONL file locally). Logging
never interrupts a job: failures are written to the server log and skipped.

Jobs describe themselves with `context.set(...)`; asyncio tasks inherit it, and a
task that sets it only changes its own copy (so the parallel reflection and deep
dive each log their own purpose).
"""

import contextvars
import logging
import time

log = logging.getLogger(__name__)

# {"user_id", "email", "retreat_id", "day", "purpose"}
context: contextvars.ContextVar[dict | None] = contextvars.ContextVar("llm_call_context", default=None)

MAX_TEXT = 200_000  # characters kept per prompt or response


# What a job is doing right now, as one short line for the page ("Day 3: recording the
# deep dive"). The pipeline sets a hook per retreat; steps and recordings call activity().
activity_hook: contextvars.ContextVar = contextvars.ContextVar("activity_hook", default=None)


def activity(text: str) -> None:
    hook = activity_hook.get()
    if hook:
        hook(text)


def tag(**fields) -> None:
    """Add to the current task's log context."""
    context.set({**(context.get() or {}), **fields})


def _clip(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + f"\n…[{len(text) - MAX_TEXT} more characters]"


def redact_images(messages: list) -> list:
    """Replace base64 image data with a short description."""

    def block(b):
        if isinstance(b, dict) and b.get("type") == "image" and "source" in b:
            src = b.get("source", {})
            size = len(src.get("data", "")) * 3 // 4
            return {"type": "image", "media_type": src.get("media_type"), "bytes": size}
        if isinstance(b, dict) and b.get("type") == "image_url":
            url = b.get("image_url", {}).get("url", "")
            return {"type": "image_url", "bytes": len(url) * 3 // 4}
        if isinstance(b, dict) and b.get("type") == "text":
            return {"type": "text", "text": _clip(b.get("text", ""))}
        return b if not hasattr(b, "model_dump") else b.model_dump(exclude_none=True)

    out = []
    for m in messages:
        content = m.get("content")
        out.append({**m, "content": [block(b) for b in content] if isinstance(content, list) else _clip(str(content))})
    return out


class Timer:
    def __init__(self):
        self.start = time.monotonic()

    @property
    def ms(self) -> int:
        return int((time.monotonic() - self.start) * 1000)


async def record(
    *,
    provider: str,
    model: str,
    system: str,
    messages: list,
    response_text: str | None,
    response_extra: dict | None,
    usage: dict,
    duration_ms: int,
    error: str | None = None,
    purpose: str | None = None,
) -> None:
    from .storage import store  # late import: storage imports config only

    ctx = context.get() or {}
    row = {
        "user_id": ctx.get("user_id"),
        "email": ctx.get("email"),
        "retreat_id": ctx.get("retreat_id"),
        "day": ctx.get("day"),
        "purpose": purpose or ctx.get("purpose", "unknown"),
        "provider": provider,
        "model": model,
        "request": {"system": _clip(system), "messages": redact_images(messages)},
        "response_text": _clip(response_text) if response_text is not None else None,
        "response": response_extra or {},
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "web_searches": usage.get("web_searches", 0),
        "usd": round(usage.get("usd", 0.0), 6),
        "duration_ms": duration_ms,
        "status": "error" if error else "ok",
        "error": error,
    }
    try:
        await store.log_llm_call(row)
    except Exception:
        log.exception("couldn't log an LLM call")


async def step(text: str, **details) -> None:
    """A step in making a retreat ("Day 2: writing the deep dive..."), logged alongside
    the calls so a build can be watched live and followed afterwards, and shown (its
    first sentence) as the retreat's current activity."""
    activity(text.split(". ")[0].rstrip("."))
    await record(provider="app", model="pipeline", system="", messages=[], response_text=text,
                 response_extra=details or None, usage={}, duration_ms=0, purpose="step")
