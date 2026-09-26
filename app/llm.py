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

from . import config, jetstream, llm_log, pricing, prompts, search
from .extract import Extracted

log = logging.getLogger(__name__)



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


async def _call(meter: pricing.Meter, **params) -> anthropic.types.Message:
    """Stream a request (long outputs) and continue server-tool turns that pause.
    Every call is logged to llm_calls, including failures."""
    messages = list(params.pop("messages"))
    request_messages = list(messages)
    timer = llm_log.Timer()
    before = meter.summary()
    searches: list = []
    message = None
    error: str | None = None
    try:
        try:
            for _ in range(4):
                async with client().messages.stream(
                    model=pricing.api_model(meter.model), messages=messages, **params
                ) as stream:
                    message = await stream.get_final_message()
                meter.add(message.usage)
                searches += [b.input for b in message.content if b.type == "server_tool_use"]
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
    except Exception as exc:
        error = getattr(exc, "message", None) or str(exc)
        raise
    finally:
        after = meter.summary()
        await llm_log.record(
            provider=config.LLM_MODE,
            model=meter.model,
            system=params.get("system", ""),
            messages=request_messages,
            response_text=_text(message) if message else None,
            response_extra={
                "stop_reason": message.stop_reason if message else None,
                "web_search_queries": searches,
                "output_format": "json_schema" if "output_config" in params else None,
                "tools": [t.get("type") for t in params.get("tools", [])],
            },
            usage={k: after[k] - before[k] for k in ("input_tokens", "output_tokens", "web_searches", "usd")},
            duration_ms=timer.ms,
            error=error,
        )


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


async def plan_retreat(source: Extracted, filename: str, instructions: str, meter: pricing.Meter) -> dict:
    if config.LLM_MODE == "stub":
        return stub_plan(source, filename)
    if pricing.is_jetstream(meter.model):
        return await _plan_jetstream(source, filename, instructions, meter)

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
            meter,
            **params, output_config={"format": {"type": "json_schema", "schema": prompts.PLAN_SCHEMA}}
        )
    except anthropic.BadRequestError:
        # Some gateways don't pass structured outputs through; ask for JSON in the prompt instead.
        log.warning("structured output rejected; retrying with a JSON instruction")
        params["system"] += "\n\nReply with only a JSON object matching this schema:\n" + json.dumps(prompts.PLAN_SCHEMA)
        try:
            message = await _call(meter, **params)
        except anthropic.BadRequestError as exc:
            raise LLMError(f"The model provider rejected the request: {exc.message}") from exc
    return _clean_plan(_parse_json(_text(message)), len(source.images))


async def _plan_jetstream(source: Extracted, filename: str, instructions: str, meter: pricing.Meter) -> dict:
    """Open models don't take a JSON schema here, so the schema goes in the prompt.
    Images are offered to the model; if they're refused, it plans from the text."""
    system = (
        instructions + "\n\n" + prompts.PLAN_FIXED.format(max_days=config.MAX_DAYS)
        + "\n\nReply with only a JSON object, no other text, matching this schema:\n" + json.dumps(prompts.PLAN_SCHEMA)
    )
    notes = [f"Image {i}" + (f" from page {img.page}" if img.page else "") for i, img in enumerate(source.images)]
    notes += [f"Scanned page {img.page} (no text layer)" for img in source.scanned_pages]
    text = f"Source file: {filename}\n"
    if notes:
        text += "Attached images, in order: " + "; ".join(notes) + "\n"
    text += f"\n<source>\n{source.text}\n</source>"
    images = [(img.data, img.mime) for img in source.images + source.scanned_pages]
    try:
        try:
            reply = await jetstream.complete(pricing.api_model(meter.model), system, text, meter, images=images, max_tokens=32000)
        except jetstream.ImagesRejected:
            log.warning("jetstream refused images; planning from text only")
            reply = await jetstream.complete(pricing.api_model(meter.model), system, text, meter, max_tokens=32000)
    except jetstream.JetstreamError as exc:
        raise LLMError(str(exc)) from exc
    return _clean_plan(_parse_json(reply), len(source.images))


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


async def _deep_jetstream(
    context: str, instructions: str, words: int, meter: pricing.Meter, provider: str | None
) -> tuple[str, list[str], bool]:
    """Jetstream models can't search, so the server does: the model proposes queries,
    a search service runs them, and the model writes from the results. Sources are
    limited to URLs that were actually returned."""
    model = pricing.api_model(meter.model)
    results: list[dict] = []
    research = None
    try:
        if provider and config.WEB_SEARCH:
            llm_log.tag(purpose="search_queries")
            reply = await jetstream.complete(model, prompts.SEARCH_QUERIES, context, meter, max_tokens=16000)
            queries = [q.strip(" -*0123456789.\"'\t") for q in reply.splitlines() if q.strip()][:3]
            llm_log.tag(purpose="research")
            research = await search.search(queries or [context.splitlines()[2]], provider)
            results = research.results
            meter.searches += research.queries
            meter.usd += research.usd
            llm_log.tag(purpose="deep")
        note = prompts.SEARCH_RESULTS if results else prompts.SEARCH_OFF
        system = instructions + "\n\n" + prompts.DEEP_FIXED.format(search_note=note, words=words)
        user = context + ("\n\n" + search.as_prompt(results) if results else "")
        reply = await jetstream.complete(model, system, user, meter)
    except jetstream.JetstreamError as exc:
        raise LLMError(str(exc)) from exc
    script, sources = _split_script(reply)
    if results:
        urls = {r["url"] for r in results}
        sources = [line for line in sources if any(u in line for u in urls)]  # drop anything not from the results
    return script, sources, (research.provider or False) if results else False  # the service that answered


def _split_script(text: str) -> tuple[str, list[str]]:
    script = re.search(r"<script>(.*?)</script>", text, re.S)
    sources = re.search(r"<sources>(.*?)</sources>", text, re.S)
    body = (script.group(1) if script else re.sub(r"<sources>.*", "", text, flags=re.S)).strip()
    lines = [ln.strip(" -*\t") for ln in (sources.group(1).splitlines() if sources else [])]
    return body, [ln for ln in lines if ln]


async def write_heart(context: str, instructions: str, words: int, meter: pricing.Meter) -> str:
    if config.LLM_MODE == "stub":
        return f"Stub heart reflection. Sit with the passage for a moment. {context[-400:]}"
    system = instructions + "\n\n" + prompts.HEART_FIXED.format(words=words)
    if pricing.is_jetstream(meter.model):
        try:
            reply = await jetstream.complete(pricing.api_model(meter.model), system, context, meter)
        except jetstream.JetstreamError as exc:
            raise LLMError(str(exc)) from exc
        return _split_script(reply)[0]
    try:
        message = await _call(meter, system=system, max_tokens=16000, messages=[{"role": "user", "content": context}])
    except anthropic.BadRequestError as exc:
        raise LLMError(f"The model provider rejected the request: {exc.message}") from exc
    return _split_script(_text(message))[0]


async def write_deep(
    context: str, instructions: str, words: int, meter: pricing.Meter, search_provider: str | None = None
) -> tuple[str, list[str], bool]:
    """Returns (script, sources, searched)."""
    if config.LLM_MODE == "stub":
        return f"Stub deep dive on the passage. {context[:400]}", [], False

    async def attempt(search: bool):
        system = instructions + "\n\n" + prompts.DEEP_FIXED.format(search_note=prompts.SEARCH_ON if search else prompts.SEARCH_OFF, words=words)
        extra = {"tools": [pricing.web_search_tool(meter.model)]} if search else {}
        return await _call(meter, system=system, max_tokens=16000, messages=[{"role": "user", "content": context}], **extra)

    if pricing.is_jetstream(meter.model):
        return await _deep_jetstream(context, instructions, words, meter, search_provider)

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
