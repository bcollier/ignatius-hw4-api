"""Jetstream2 inference service: OpenAI-style chat completions through its Open WebUI
proxy (https://llm.jetstream-cloud.org/api), authenticated with a token generated in
the Jetstream chat UI. Used for free mode; there is no web search here."""

import base64
import logging

import httpx

from . import config

log = logging.getLogger(__name__)


class JetstreamError(RuntimeError):
    """The message is safe to show to the user."""


class ImagesRejected(JetstreamError):
    pass


async def complete(model: str, system: str, text: str, meter, images: list[tuple[bytes, str]] = (), max_tokens: int = 8000) -> str:
    """One chat completion. `images` are (bytes, mime) pairs sent as data URLs; if the
    model or proxy refuses them, ImagesRejected is raised so the caller can retry
    without them."""
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
        message = data["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError) as exc:
        raise JetstreamError("Jetstream returned a response that couldn't be read.") from exc
    usage = data.get("usage") or {}
    meter.add_tokens(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    if not message.strip():
        raise JetstreamError("Jetstream returned an empty response.")
    return message
