"""Jetstream2 inference service: OpenAI-style chat completions through its Open WebUI
proxy (https://llm.jetstream-cloud.org/api), authenticated with a token generated in
the Jetstream chat UI. Used for free mode; there is no web search here."""

import base64
import logging

import httpx

from . import config, llm_log

log = logging.getLogger(__name__)


class JetstreamError(RuntimeError):
    """The message is safe to show to the user."""


class ImagesRejected(JetstreamError):
    pass


async def complete(model: str, system: str, text: str, meter, images: list[tuple[bytes, str]] = (), max_tokens: int = 8000) -> str:
    """One chat completion. `images` are (bytes, mime) pairs sent as data URLs; if the
    model or proxy refuses them, ImagesRejected is raised so the caller can retry
    without them. Every call is logged to llm_calls."""
    timer = llm_log.Timer()
    before = meter.summary()
    result: dict = {}
    error: str | None = None
    try:
        return await _complete(model, system, text, meter, images, max_tokens, result)
    except JetstreamError as exc:
        error = str(exc)
        raise
    finally:
        after = meter.summary()
        await llm_log.record(
            provider="jetstream",
            model=meter.model,
            system=system,
            messages=[{"role": "user", "content": [{"type": "text", "text": text}] + [
                {"type": "image", "media_type": mime, "bytes": len(data)} for data, mime in images
            ]}],
            response_text=result.get("content"),
            response_extra={"reasoning": result.get("reasoning"), "finish_reason": result.get("finish_reason")},
            usage={k: after[k] - before[k] for k in ("input_tokens", "output_tokens", "web_searches", "usd")},
            duration_ms=timer.ms,
            error=error,
        )


async def _complete(model, system, text, meter, images, max_tokens, result: dict) -> str:
    content: list[dict] | str = text
    if images:
        content = [{"type": "text", "text": text}] + [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}
            for data, mime in images
        ]
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=300) as http:
            response = await http.post(
                f"{config.JETSTREAM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {config.JETSTREAM_API_KEY}"},
                json=body,
            )
    except httpx.HTTPError as exc:
        raise JetstreamError("Couldn't reach the Jetstream model service.") from exc
    if response.status_code in (401, 403):
        raise JetstreamError("Jetstream rejected the API token.")
    if response.status_code == 429:
        raise JetstreamError("Jetstream is rate limiting requests. Try again in a minute.")
    if response.status_code >= 400:
        log.warning("jetstream %s: %s", response.status_code, response.text[:300])
        if images and response.status_code in (400, 413, 415, 422):
            raise ImagesRejected("images not accepted")
        raise JetstreamError(f"Jetstream returned an error ({response.status_code}).")
    try:
        data = response.json()
        choice = data["choices"][0]
        message = choice["message"]["content"] or ""
    except (ValueError, KeyError, IndexError) as exc:
        raise JetstreamError("Jetstream returned a response that couldn't be read.") from exc
    result.update(
        content=message,
        reasoning=choice["message"].get("reasoning_content"),  # reasoning models such as Muse Glimmer
        finish_reason=choice.get("finish_reason"),
    )
    usage = data.get("usage") or {}
    meter.add_tokens(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    if not message.strip():
        raise JetstreamError("Jetstream returned an empty response.")
    return message
