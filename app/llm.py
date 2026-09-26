"""Claude calls: plan a retreat from the source, then write each day's scripts.

Claude is reached through OpenRouter's Anthropic-compatible endpoint by default,
so the official Anthropic SDK works with an OpenRouter key. With LLM_MODE=stub no
API is called, which keeps tests and frontend work free.
"""

import base64
import json
import logging
import re
from dataclasses import dataclass, field

import anthropic

from . import config, jetstream, llm_log, pricing, prompts, search, series
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


MAX_TURNS = 4  # a server-side tool (web search) may pause the reply; continue it up to this many times


@dataclass
class _Exchange:
    """What came back from one model call, gathered across paused turns (kept even if
    a later turn fails, so the log shows how far it got)."""
    message: anthropic.types.Message | None = None
    searches: list = field(default_factory=list)
    blocks: list = field(default_factory=list)  # content from every turn, for the research log


async def _call(meter: pricing.Meter, **params) -> anthropic.types.Message:
    """Stream a request (long outputs) and continue server-tool turns that pause.
    Every call is logged to llm_calls, including failures."""
    messages = list(params.pop("messages"))
    request_messages = list(messages)
    params["system"] = _with_background(params.get("system", ""))
    timer = llm_log.Timer()
    before = meter.summary()
    exchange = _Exchange()
    error: str | None = None
    try:
        await _stream_turns(meter, messages, params, exchange)
        meter.blocks = exchange.blocks
        _check_stop_reason(exchange.message)
        return exchange.message
    except Exception as exc:
        error = getattr(exc, "message", None) or str(exc)
        raise
    finally:
        await _log_call(meter, params, request_messages, exchange, before, timer, error)


async def _stream_turns(meter: pricing.Meter, messages: list, params: dict, exchange: _Exchange) -> None:
    try:
        for _ in range(MAX_TURNS):
            async with client().messages.stream(
                model=pricing.api_model(meter.model), messages=messages, **params
            ) as stream:
                message = await stream.get_final_message()
            exchange.message = message
            meter.add(message.usage)
            exchange.searches += [b.input for b in message.content if b.type == "server_tool_use"]
            exchange.blocks += list(message.content)
            if message.stop_reason != "pause_turn":
                return
            messages.append({"role": "assistant", "content": message.content})
    except anthropic.AuthenticationError as exc:
        raise LLMError("The model provider rejected the API key.") from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("The model provider is rate limiting requests. Try again in a minute.") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Couldn't reach the model provider.") from exc


def _check_stop_reason(message: anthropic.types.Message) -> None:
    if message.stop_reason == "refusal":
        raise LLMError("The model declined to write this section.")
    if message.stop_reason == "max_tokens":
        raise LLMError("The model ran out of room before finishing. Try a shorter document.")


async def _log_call(meter: pricing.Meter, params: dict, request_messages: list, exchange: _Exchange, before: dict,
                    timer: llm_log.Timer, error: str | None) -> None:
    message = exchange.message
    after = meter.summary()
    await llm_log.record(
        provider=config.LLM_MODE,
        model=meter.model,
        system=_system_text(params.get("system", "")),
        messages=request_messages,
        response_text=_text(message) if message else None,
        response_extra={
            "stop_reason": message.stop_reason if message else None,
            "web_search_queries": exchange.searches,
            "output_format": "json_schema" if "output_config" in params else None,
            "tools": [t.get("type") for t in params.get("tools", [])],
        },
        usage={k: after[k] - before[k] for k in ("input_tokens", "output_tokens", "web_searches", "usd")},
        duration_ms=timer.ms,
        error=error,
    )


def _with_background(system):
    """Every prompt starts with the background on the Exercises, retreats and lectio
    divina (prompts.BACKGROUND), then what the person wrote about themselves, if
    anything (prompts.PERSON). With a series, both join the cached first block."""
    lead = prompts.BACKGROUND + "\n\n" + (prompts.person_block(prompts.PERSON.get()) + "\n\n" if prompts.PERSON.get() else "")
    if isinstance(system, list):
        return [{**system[0], "text": lead + system[0]["text"]}] + system[1:]
    return lead + system


def _system(instructions: str, series_text: str):
    """The system prompt, with an earlier series (if any) as a separate block first,
    marked for prompt caching: it's identical across the days of a week, so later
    calls read it from cache at a fraction of the price."""
    if not series_text:
        return instructions
    return [
        {"type": "text", "text": series.INSTRUCTIONS + "\n\n" + series_text, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": instructions},
    ]


def _with_series(text: str, series_text: str) -> str:
    """For Jetstream (no system blocks or caching): the series goes before the request."""
    return f"{series.INSTRUCTIONS}\n\n{series_text}\n\n{text}" if series_text else text


def _system_text(system) -> str:
    return system if isinstance(system, str) else "\n\n".join(b.get("text", "") for b in system)


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


async def plan_retreat(
    source: Extracted, filename: str, instructions: str, meter: pricing.Meter, series_text: str = ""
) -> dict:
    if config.LLM_MODE == "stub":
        return stub_plan(source, filename)
    if pricing.is_jetstream(meter.model):
        return await _plan_jetstream(source, filename, instructions, meter, series_text)

    content: list[dict] = []
    for i, img in enumerate(source.images):
        where = f" from page {img.page}" if img.page else ""
        content += [{"type": "text", "text": f"Image {i}{where}:"}, _image_block(img.data, img.mime)]
    for img in source.scanned_pages:
        content += [{"type": "text", "text": f"Scanned page {img.page} (no text layer):"}, _image_block(img.data, img.mime)]
    content.append({"type": "text", "text": f"Source file: {filename}\n\n<source>\n{source.text}\n</source>"})

    system = _system(instructions + "\n\n" + prompts.PLAN_FIXED.format(max_days=config.MAX_DAYS), series_text)
    params = dict(system=system, max_tokens=64000, messages=[{"role": "user", "content": content}])
    try:
        message = await _call(
            meter,
            **params, output_config={"format": {"type": "json_schema", "schema": prompts.PLAN_SCHEMA}}
        )
    except anthropic.BadRequestError:
        # Some gateways don't pass structured outputs through; ask for JSON in the prompt instead.
        log.warning("structured output rejected; retrying with a JSON instruction")
        schema_note = "\n\nReply with only a JSON object matching this schema:\n" + json.dumps(prompts.PLAN_SCHEMA)
        if isinstance(params["system"], str):
            params["system"] += schema_note
        else:
            params["system"][-1]["text"] += schema_note
        try:
            message = await _call(meter, **params)
        except anthropic.BadRequestError as exc:
            raise LLMError(f"The model provider rejected the request: {exc.message}") from exc
    return _clean_plan(_parse_json(_text(message)), len(source.images))


async def _plan_jetstream(
    source: Extracted, filename: str, instructions: str, meter: pricing.Meter, series_text: str = ""
) -> dict:
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
    text = _with_series(text, series_text)
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
        indexes = [i for i in day.get("image_indexes") or [] if isinstance(i, int) and 0 <= i < image_count]
        first = day.get("image_index", -1)
        if isinstance(first, int) and 0 <= first < image_count and first not in indexes:
            indexes.insert(0, first)
        day["image_indexes"] = list(dict.fromkeys(indexes))
        day["image_index"] = day["image_indexes"][0] if day["image_indexes"] else -1
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
            "image_indexes": [i] if i < len(source.images) else [],
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
    context: str, instructions: str, words: int, meter: pricing.Meter, provider: str | None, series_text: str = ""
) -> tuple[str, list[str], bool]:
    """Jetstream models can't search, so the server does: the model proposes queries,
    a search service runs them, and the model writes from the results. Sources are
    limited to URLs that were actually returned."""
    model = pricing.api_model(meter.model)
    research = None
    try:
        if provider and config.WEB_SEARCH:
            research = await _server_research(model, context, meter, provider)
        results = research.results if research else []
        system = _deep_system(instructions, words, bool(results), search_on=False)
        reply = await jetstream.complete(model, system, _with_series(_deep_user(context, results), series_text), meter)
    except jetstream.JetstreamError as exc:
        raise LLMError(str(exc)) from exc
    script, sources = _split_script(reply)
    if results:
        urls = {r["url"] for r in results}
        sources = [line for line in sources if any(u in line for u in urls)]  # drop anything not from the results
    if research is not None:
        meter.research = _research_record(research, results)
    return script, sources, (research.provider or False) if results else False  # the service that answered


async def _server_research(model: str, context: str, meter: pricing.Meter, provider: str):
    """The Jetstream model writes the queries; the search services run them."""
    llm_log.tag(purpose="search_queries")
    reply = await jetstream.complete(model, prompts.SEARCH_QUERIES, context, meter, max_tokens=16000)
    queries = _queries(reply) or [_passage_ref(context)]
    llm_log.tag(purpose="research")
    research = await search.search(queries, provider)
    research.query_texts = queries
    meter.searches += research.queries
    meter.usd += research.usd
    llm_log.tag(purpose="deep")
    return research


def _passage_ref(context: str) -> str:
    """The day context's third line (the source reference), a fallback search query."""
    return context.splitlines()[2]


def _deep_system(instructions: str, words: int, has_results: bool, search_on: bool) -> str:
    """The deep dive's instructions, with a note on what research it has to work with."""
    if has_results:
        note = prompts.SEARCH_BOTH if search_on else prompts.SEARCH_RESULTS
    else:
        note = prompts.SEARCH_ON if search_on else prompts.SEARCH_OFF
    return instructions + "\n\n" + prompts.DEEP_FIXED.format(search_note=note, words=words)


def _deep_user(context: str, results: list[dict]) -> str:
    return context + ("\n\n" + search.as_prompt(results) if results else "")


def _research_record(research, results: list[dict], own: dict | None = None) -> dict:
    """What the research page shows for the day: the server's searches and results,
    plus (for Claude) the searches it ran itself."""
    own_queries = own["queries"] if own else []
    own_results = own["results"] if own else []
    return {
        "how": "server search" + (" + model web search" if own_queries else ""),
        "service": research.provider or None,
        "queries": research.query_texts + own_queries,
        "contributors": research.contributors + (["Claude web search"] if own_results else []),
        "skipped": research.skipped,
        "results": results + own_results,
    }


async def tailor_guide(context: str, heart: str, deep: str, lines: dict, meter: pricing.Meter) -> dict:
    """Adapt the spoken guidance to this day's reflection and deep dive. Returns the
    lines to use; any line the model drops, empties or badly overruns keeps its default,
    and any failure returns the defaults unchanged (tailoring is never required)."""
    if config.LLM_MODE == "stub" or not lines:
        return dict(lines)
    user = prompts.tailor_input(context, heart, deep, lines)
    schema = {"type": "object", "properties": {k: {"type": "string"} for k in lines},
              "required": list(lines), "additionalProperties": False}
    try:
        if pricing.is_jetstream(meter.model):
            reply = await jetstream.complete(pricing.api_model(meter.model), prompts.GUIDE_TAILOR, user, meter)
        else:
            try:
                message = await _call(meter, system=prompts.GUIDE_TAILOR, max_tokens=16000, messages=[{"role": "user", "content": user}],
                                      output_config={"format": {"type": "json_schema", "schema": schema}})
            except anthropic.BadRequestError:
                message = await _call(meter, system=prompts.GUIDE_TAILOR, max_tokens=16000, messages=[{"role": "user", "content": user}])
            reply = _text(message)
        tailored = _parse_json(reply)
    except Exception:  # LLMError, JetstreamError, bad JSON: keep the defaults
        log.warning("couldn't tailor the spoken guidance; using the default lines")
        return dict(lines)
    out = {}
    for name, default in lines.items():
        text = " ".join(str(tailored.get(name) or "").split())
        ok = 0 < len(text) <= max(2 * len(default), 600)
        if ok and name == "opening" and "Ask for" in default:
            grace = default[default.index("Ask for"):].split(". ")[0]
            ok = grace in text  # the request for the grace must survive word for word
        out[name] = text if ok else default
    return out


async def condense_text(instructions: str, text: str, meter: pricing.Meter) -> str:
    """A plain summarizing call (used for long profile notes)."""
    if config.LLM_MODE == "stub":
        return text[: config.PROFILE_MAX_CHARS]
    if pricing.is_jetstream(meter.model):
        return await jetstream.complete(pricing.api_model(meter.model), instructions, text, meter)
    message = await _call(meter, system=instructions, max_tokens=8000, messages=[{"role": "user", "content": text}])
    return _text(message)


def _split_script(text: str) -> tuple[str, list[str]]:
    script = re.search(r"<script>(.*?)</script>", text, re.S)
    sources = re.search(r"<sources>(.*?)</sources>", text, re.S)
    body = (script.group(1) if script else re.sub(r"<sources>.*", "", text, flags=re.S)).strip()
    lines = [ln.strip(" -*\t") for ln in (sources.group(1).splitlines() if sources else [])]
    return body, [ln for ln in lines if ln]


async def write_heart(context: str, instructions: str, words: int, meter: pricing.Meter, series_text: str = "") -> str:
    if config.LLM_MODE == "stub":
        return f"Stub heart reflection. Sit with the passage for a moment. {context[-400:]}"
    system = instructions + "\n\n" + prompts.HEART_FIXED.format(words=words)
    if pricing.is_jetstream(meter.model):
        try:
            reply = await jetstream.complete(pricing.api_model(meter.model), system, _with_series(context, series_text), meter)
        except jetstream.JetstreamError as exc:
            raise LLMError(str(exc)) from exc
        return _split_script(reply)[0]
    try:
        message = await _call(meter, system=_system(system, series_text), max_tokens=32000,
                              messages=[{"role": "user", "content": context}])
    except anthropic.BadRequestError as exc:
        raise LLMError(f"The model provider rejected the request: {exc.message}") from exc
    return _split_script(_text(message))[0]


async def write_deep(
    context: str, instructions: str, words: int, meter: pricing.Meter, search_provider: str | None = None,
    series_text: str = "",
) -> tuple[str, list[str], bool]:
    """Returns (script, sources, searched). `searched` names the search service(s)
    when the free services found results, otherwise whether the model searched."""
    if config.LLM_MODE == "stub":
        return f"Stub deep dive on the passage. {context[:400]}", [], False
    if pricing.is_jetstream(meter.model):
        return await _deep_jetstream(context, instructions, words, meter, search_provider, series_text)
    return await _deep_claude(context, instructions, words, meter, search_provider, series_text)


async def _deep_claude(
    context: str, instructions: str, words: int, meter: pricing.Meter, search_provider: str | None, series_text: str
) -> tuple[str, list[str], bool]:
    # The free search services go first as a head start; Claude still searches as much
    # as it needs (its full allowance), so weak free results never limit the deep dive.
    research = await _free_research(context, meter, search_provider) if config.WEB_SEARCH else None
    results = research.results if research else []

    async def attempt(search_on: bool):
        extra = {"tools": [pricing.web_search_tool(meter.model)]} if search_on else {}
        return await _call(meter, system=_system(_deep_system(instructions, words, bool(results), search_on), series_text),
                           max_tokens=32000, messages=[{"role": "user", "content": _deep_user(context, results)}], **extra)

    message, searched = await _with_search_fallback(attempt)
    # With web search the reply is split into many text blocks around the search
    # results; the <script> tags mark the part to read aloud.
    script, sources = _split_script(_text(message))
    own = _web_search_log(getattr(meter, "blocks", None) or message.content) if searched else None
    if research is not None:
        meter.research = _research_record(research, results, own)
    elif own:
        meter.research = own
    if results:
        return script, sources, research.provider or True  # names the free service(s) for the page
    return script, sources, searched


async def _with_search_fallback(attempt):
    """Try with web search (when on); if the provider refuses the search tool, write
    without it rather than fail. Returns (message, whether web search was used)."""
    searched = config.WEB_SEARCH
    try:
        return await attempt(searched), searched
    except anthropic.BadRequestError as exc:
        if not searched:
            raise LLMError(f"The model provider rejected the request: {exc.message}") from exc
        log.warning("web search rejected (%s); writing the deep dive without it", exc.message)
    try:
        return await attempt(False), False
    except anthropic.BadRequestError as exc2:
        raise LLMError(f"The model provider rejected the request: {exc2.message}") from exc2


def _queries(reply: str) -> list[str]:
    """Up to three queries, one per line, without list numbering, bullets or quotes."""
    lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", q).strip(" \"'\t") for q in reply.splitlines()]
    return [q for q in lines if q][:3]


async def _free_research(context: str, meter: pricing.Meter, provider: str | None):
    """Queries written by the model, run through the free search services. None when
    no service is chosen or configured; never fails the day."""
    if not provider or provider == "none" or not search.available():
        return None
    try:
        llm_log.tag(purpose="search_queries")
        message = await _call(meter, system=prompts.SEARCH_QUERIES, max_tokens=1000,
                              messages=[{"role": "user", "content": context}])
        queries = _queries(_text(message))
        llm_log.tag(purpose="research")
        research = await search.search(queries or [_passage_ref(context)], provider)
        research.query_texts = queries
        meter.searches += research.queries
        meter.usd += research.usd
        return research
    except Exception:
        log.warning("free research failed; the model will search on its own", exc_info=True)
        return None
    finally:
        llm_log.tag(purpose="deep")


def _web_search_log(blocks) -> dict:
    """The searches Claude ran with the web search tool and the pages it drew on."""
    queries, results = [], []

    def add(url, title, text):
        seen = next((x for x in results if x["url"] == url), None)
        if seen:
            if text and text[:300] not in seen["content"]:
                seen["content"] = (seen["content"] + " … " + text).strip(" …")[:search.SNIPPET_CHARS]
        elif url:
            results.append({"title": title or url, "url": url, "content": text[:search.SNIPPET_CHARS],
                            "service": "Claude web search"})
    for block in blocks:
        kind = getattr(block, "type", "")
        if kind == "server_tool_use":
            q = (getattr(block, "input", None) or {}).get("query")
            if q:
                queries.append(q)
        elif kind == "web_search_tool_result" and isinstance(getattr(block, "content", None), list):
            for r in block.content:
                add(getattr(r, "url", None), getattr(r, "title", None), "")
        elif kind == "text":
            # Through OpenRouter the results arrive only as citations on the text.
            for c in getattr(block, "citations", None) or []:
                add(getattr(c, "url", None), getattr(c, "title", None), getattr(c, "cited_text", None) or "")
    return {"how": "model web search", "service": "Claude web search", "queries": queries, "results": results,
            "contributors": [], "skipped": []}
