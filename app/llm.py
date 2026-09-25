"""Claude calls: plan a retreat from the source, then write each day's scripts.

Claude is reached through OpenRouter's Anthropic-compatible endpoint by default,
so the official Anthropic SDK works with an OpenRouter key. With LLM_MODE=stub no
API is called, which keeps tests and frontend work free.
"""

import base64
import json
import logging
import re

import anthropic

from . import config, prompts
from .extract import Extracted

log = logging.getLogger(__name__)

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}


class LLMError(RuntimeError):
    """A model call failed; the message is safe to show to the user."""


_client: anthropic.AsyncAnthropic | None = None


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        if config.LLM_MODE == "openrouter":
            _client = anthropic.AsyncAnthropic(
                base_url="https://openrouter.ai/api", auth_token=config.OPENROUTER_API_KEY, timeout=300
            )
        else:
            _client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=300)
    return _client


async def _call(**params) -> anthropic.types.Message:
    """Stream a request (long outputs) and continue server-tool turns that pause."""
    messages = list(params.pop("messages"))
    try:
        for _ in range(4):
            async with client().messages.stream(model=config.LLM_MODEL, messages=messages, **params) as stream:
                message = await stream.get_final_message()
            if message.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": message.content})
    except anthropic.AuthenticationError as exc:
        raise LLMError("The model provider rejected the API key.") from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("The model provider is rate limiting requests. Try again in a minute.") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Couldn't reach the model provider.") from exc
    if message.stop_reason == "refusal":
        raise LLMError("The model declined to write this section.")
    if message.stop_reason == "max_tokens":
        raise LLMError("The model ran out of room before finishing. Try a shorter document.")
    return message


def _text(message: anthropic.types.Message) -> str:
    return "".join(block.text for block in message.content if block.type == "text")


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise LLMError("The model didn't return a retreat plan.")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError("The model returned a plan that couldn't be read.") from exc


def _image_block(data: bytes, mime: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.standard_b64encode(data).decode()}}


# ---------------------------------------------------------------- planning


async def plan_retreat(source: Extracted, filename: str, instructions: str) -> dict:
    if config.LLM_MODE == "stub":
        return stub_plan(source, filename)

    content: list[dict] = []
    for i, img in enumerate(source.images):
        where = f" from page {img.page}" if img.page else ""
        content += [{"type": "text", "text": f"Image {i}{where}:"}, _image_block(img.data, img.mime)]
    for img in source.scanned_pages:
        content += [{"type": "text", "text": f"Scanned page {img.page} (no text layer):"}, _image_block(img.data, img.mime)]
    content.append({"type": "text", "text": f"Source file: {filename}\n\n<source>\n{source.text}\n</source>"})

    system = instructions + "\n\n" + prompts.PLAN_FIXED.format(max_days=config.MAX_DAYS)
    params = dict(system=system, max_tokens=32000, messages=[{"role": "user", "content": content}])
    try:
        message = await _call(
            **params, output_config={"format": {"type": "json_schema", "schema": prompts.PLAN_SCHEMA}}
        )
    except anthropic.BadRequestError:
        # Some gateways don't pass structured outputs through; ask for JSON in the prompt instead.
        log.warning("structured output rejected; retrying with a JSON instruction")
        params["system"] += "\n\nReply with only a JSON object matching this schema:\n" + json.dumps(prompts.PLAN_SCHEMA)
        try:
            message = await _call(**params)
        except anthropic.BadRequestError as exc:
            raise LLMError(f"The model provider rejected the request: {exc.message}") from exc
    return _clean_plan(_parse_json(_text(message)), len(source.images))


def _clean_plan(plan: dict, image_count: int) -> dict:
    days = [d for d in plan.get("days", []) if d.get("passage_text", "").strip()][: config.MAX_DAYS]
    if not days:
        raise LLMError("The model couldn't find any usable passages in this document.")
    for n, day in enumerate(days, start=1):
        day["day"] = n
        if not 0 <= day.get("image_index", -1) < image_count:
            day["image_index"] = -1
    plan["days"] = days
    return plan


def stub_plan(source: Extracted, filename: str) -> dict:
    """A rough plan without a model: split on "Day N" headings, else on paragraphs."""
    text = re.sub(r"\[Page \d+\]\n", "", source.text)
    chunks = re.split(r"(?im)^\s*day\s+\d+\b[^\n]*\n", text)
    headings = re.findall(r"(?im)^\s*(day\s+\d+\b[^\n]*)\n", text)
    if len(headings) >= 2:
        mode, pairs = "follows_source", list(zip(headings, chunks[1:]))
    else:
        mode = "composed"
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()] or ["(No text found.)"]
        pairs = [(f"Day {i + 1}", paragraphs[i % len(paragraphs)]) for i in range(config.DEFAULT_DAYS)]
    days = []
    for i, (title, body) in enumerate(pairs[: config.MAX_DAYS]):
        days.append({
            "day": i + 1,
            "title": title.strip(),
            "source_ref": filename,
            "passage_text": body.strip()[:1500],
            "grace": "To know God's closeness in this passage.",
            "focus": "Stub plan: set OPENROUTER_API_KEY for a real one.",
            "image_index": i if i < len(source.images) else -1,
        })
    return {
        "title": filename.rsplit(".", 1)[0],
        "summary": "Planned without a model (LLM_MODE=stub).",
        "mode": mode,
        "images": [{"index": i, "description": f"Image {i}"} for i in range(len(source.images))],
        "days": days,
    }


# ---------------------------------------------------------------- scripts


def _split_script(text: str) -> tuple[str, list[str]]:
    script = re.search(r"<script>(.*?)</script>", text, re.S)
    sources = re.search(r"<sources>(.*?)</sources>", text, re.S)
    body = (script.group(1) if script else re.sub(r"<sources>.*", "", text, flags=re.S)).strip()
    lines = [ln.strip(" -*\t") for ln in (sources.group(1).splitlines() if sources else [])]
    return body, [ln for ln in lines if ln]


async def write_heart(context: str, instructions: str, words: int) -> str:
    if config.LLM_MODE == "stub":
        return f"Stub heart reflection. Sit with the passage for a moment. {context[-400:]}"
    system = instructions + "\n\n" + prompts.HEART_FIXED.format(words=words)
    try:
        message = await _call(system=system, max_tokens=16000, messages=[{"role": "user", "content": context}])
    except anthropic.BadRequestError as exc:
        raise LLMError(f"The model provider rejected the request: {exc.message}") from exc
    return _split_script(_text(message))[0]


async def write_deep(context: str, instructions: str, words: int) -> tuple[str, list[str], bool]:
    """Returns (script, sources, searched)."""
    if config.LLM_MODE == "stub":
        return f"Stub deep dive on the passage. {context[:400]}", [], False

    async def attempt(search: bool):
        system = instructions + "\n\n" + prompts.DEEP_FIXED.format(search_note=prompts.SEARCH_ON if search else prompts.SEARCH_OFF, words=words)
        extra = {"tools": [WEB_SEARCH_TOOL]} if search else {}
        return await _call(system=system, max_tokens=16000, messages=[{"role": "user", "content": context}], **extra)

    searched = config.WEB_SEARCH
    try:
        message = await attempt(searched)
    except anthropic.BadRequestError as exc:
        if not searched:
            raise LLMError(f"The model provider rejected the request: {exc.message}") from exc
        log.warning("web search rejected (%s); writing the deep dive without it", exc.message)
        searched = False
        try:
            message = await attempt(False)
        except anthropic.BadRequestError as exc2:
            raise LLMError(f"The model provider rejected the request: {exc2.message}") from exc2
    # With web search the reply is split into many text blocks around the search
    # results; the <script> tags mark the part to read aloud.
    script, sources = _split_script(_text(message))
    return script, sources, searched
