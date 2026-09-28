"""A first look at an uploaded document, before anything is made: a working title and
two or three sentences on what it is, so the person can see they chose the right file
rather than a file name like "P1W3P.pdf". One short model call on the start of the
document, with the planning model the person chose; nothing is stored."""

import json
import logging
import re

from . import config, jetstream, llm, llm_log, pricing, prompts
from .extract import Extracted

log = logging.getLogger(__name__)
PREVIEW_CHARS = 6000  # the start of the document is enough to say what it is
MIN_TEXT = 200  # below this, a scanned page or photo is read instead
MAX_TITLE = 90
MAX_DESCRIPTION = 700
# A quick answer matters more here than the best writing, so the preview uses the fast
# model from the same family as the one chosen (the document stays with that provider).
FAST_FREE = "jetstream/llama-4-scout"
FAST_CLAUDE = "anthropic/claude-haiku-4.5"


def fast_model(model: str) -> str:
    if pricing.is_jetstream(model):
        return FAST_FREE if FAST_FREE in {m for m, _ in pricing.jetstream_models()} else model
    return FAST_CLAUDE if model.startswith("anthropic/") and FAST_CLAUDE in pricing.model_ids() else model


def instructions() -> str:
    return prompts.custom("preview", prompts._prompt("preview"))


async def describe(filename: str, source: Extracted, model: str) -> dict:
    """{"title", "description", "pages"}; raises on failure (the caller shows the file name)."""
    text = source.text.strip()[:PREVIEW_CHARS]
    image = None
    if len(text) < MIN_TEXT:
        pictures = source.scanned_pages or source.images
        image = pictures[0] if pictures else None
    model = fast_model(model)
    if config.LLM_MODE == "stub":
        reply = _stub(text)
    else:
        llm_log.tag(purpose="preview")
        user = f"File name: {filename}\n\n<document_start>\n{text or '(no text layer)'}\n</document_start>"
        meter = pricing.Meter(model, await pricing.prices())
        if pricing.is_jetstream(model):
            reply = await jetstream.complete(pricing.api_model(model), instructions(), user, meter,
                                             images=[(image.data, image.mime)] if image else (), max_tokens=4000)
        else:
            content = ([llm._image_block(image.data, image.mime)] if image else []) + [{"type": "text", "text": user}]
            message = await llm._call(meter, system=instructions(), max_tokens=600, messages=[{"role": "user", "content": content}])
            reply = llm._text(message)
    found = _parse(reply)
    return {"title": found["title"], "description": found["description"], "pages": source.page_count}


def _parse(reply: str) -> dict:
    match = re.search(r"\{.*\}", reply, re.S)
    data = json.loads(match.group(0)) if match else {}
    title = " ".join(str(data.get("title") or "").split()).strip("\"'“”")[:MAX_TITLE]
    description = " ".join(str(data.get("description") or "").split())[:MAX_DESCRIPTION]
    if not title or not description:
        raise ValueError("the model didn't describe the document")
    return {"title": title, "description": description}


def _stub(text: str) -> str:
    """Without a model (tests, local runs): the first line and the first sentences."""
    lines = [line.strip() for line in text.splitlines() if line.strip() and not re.fullmatch(r"\[Page \d+\]", line.strip())]
    title = lines[0][:60] if lines else "Untitled document"
    rest = " ".join(lines[1:])
    description = " ".join(re.split(r"(?<=[.!?])\s+", rest)[:2]) or "The document has little readable text."
    return json.dumps({"title": title, "description": description})
