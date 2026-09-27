"""A retreat from an idea ("the parables of Jesus, seven days") and, if they like, a photo
(a page of verses, a prayer card, a painting, a view). A model chooses a passage for
each day; the scripture itself is never written by the model: it's copied from the
photo when the photo shows it, or fetched from the World English Bible (public domain)
through bible-api.com. The result is an ordinary source document, planned and made
like any upload."""

import asyncio
import json
import logging
import re
from urllib.parse import quote

import httpx

from . import config, jetstream, llm, llm_log, pricing, prompts
from .extract import Extracted, Image

log = logging.getLogger(__name__)

BIBLE_API = "https://bible-api.com/{ref}?translation=web"
MAX_IDEA = 4_000
SYSTEM = prompts._prompt("inspiration")

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "idea": {"type": "string"},
        "days": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "reference": {"type": "string"},
                "from_photo": {"type": "string"},
                "why": {"type": "string"},
            },
            "required": ["title", "reference", "from_photo", "why"],
            "additionalProperties": False,
        }},
    },
    "required": ["title", "idea", "days"],
    "additionalProperties": False,
}


class InspirationError(RuntimeError):
    """The idea couldn't be turned into a retreat; the message is safe to show."""


async def compose(idea: str, photo: Image | None, days: int, model: str) -> tuple[str, Extracted]:
    """The source document for the retreat (a filename and its text), with the photo kept
    as an image so the planner and writers can see it too."""
    meter = pricing.Meter(model, await pricing.prices())
    llm_log.tag(purpose="inspiration")
    await llm_log.step(f"Choosing {days} passages for the idea" + (" and the photo" if photo else "") + ".")
    outline = await _outline(idea, photo, days, model, meter)
    texts = await asyncio.gather(*(_passage(d) for d in outline["days"]))
    parts = [f"{outline['title']}\n\nA retreat of {len(texts)} days, from this idea: {outline['idea']}\n"
             "Scripture is from the World English Bible (public domain) unless it was copied from the person's photo."]
    for n, (day, (ref, text)) in enumerate(zip(outline["days"], texts), start=1):
        parts.append(f"Day {n}: {day['title']}\n{ref}\n\n{text}")
    await llm_log.step(f"Fetched the scripture for {len(texts)} days from the World English Bible.")
    source = Extracted(kind="idea", text="\n\n".join(parts), page_count=0,
                       images=[photo] if photo else [])
    return _filename(outline["title"]), source


async def _outline(idea: str, photo: Image | None, days: int, model: str, meter: pricing.Meter) -> dict:
    ask = (f"<idea>\n{idea.strip() or '(No words; the photo is the inspiration.)'}\n</idea>\n\n"
           f"Make {days} days.")
    if pricing.is_jetstream(model):
        system = prompts.custom("inspiration", SYSTEM) + "\n\nReply with only a JSON object, no other text, matching this schema:\n" + json.dumps(SCHEMA)
        images = [(photo.data, photo.mime)] if photo else []
        try:
            reply = await jetstream.complete(pricing.api_model(model), system, ask, meter, images=images, max_tokens=4000)
        except jetstream.ImagesRejected:
            raise InspirationError("The free model can't look at photos. Describe the idea in words, "
                                   "or upload the photo as the retreat's material instead.")
        except jetstream.JetstreamError as exc:
            raise InspirationError(str(exc)) from exc
        outline = llm._parse_json(reply)
    else:
        content = ([{"type": "text", "text": "The photo:"}, llm._image_block(photo.data, photo.mime)] if photo else [])
        content.append({"type": "text", "text": ask})
        message = await llm._call(meter, system=prompts.custom("inspiration", SYSTEM), max_tokens=8000, messages=[{"role": "user", "content": content}],
                                  output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
        outline = llm._parse_json(llm._text(message))
    outline["days"] = [d for d in outline.get("days", []) if d.get("reference") or d.get("from_photo")][: config.MAX_DAYS]
    if not outline["days"]:
        raise InspirationError("No passages could be chosen for that idea. Try saying a little more about it.")
    return outline


async def _passage(day: dict) -> tuple[str, str]:
    """(reference line, text) for a day: the photo's own words, or the World English Bible."""
    if day["from_photo"].strip():
        return f"{day['reference']} (as printed in the photo)", day["from_photo"].strip()
    ref = day["reference"]
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            response = await http.get(BIBLE_API.format(ref=quote(_plain_ref(ref))))
        response.raise_for_status()
        found = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("bible-api couldn't find %s: %s", ref, exc)
        raise InspirationError(f"Couldn't find the text of {ref}. Please try again, or name the passages yourself.") from exc
    text = " ".join(v["text"].strip() for v in found["verses"])
    return f"{found['reference']} (World English Bible)", re.sub(r"\s+", " ", text).strip()


def _plain_ref(ref: str) -> str:
    """bible-api wants "John 4:5-26": no translation note, and a hyphen for the range."""
    return re.sub(r"\s*\(.*?\)", "", ref).replace("–", "-").replace("—", "-").strip()


def _filename(title: str) -> str:
    return (re.sub(r"[^\w\s-]", "", title).strip() or "My retreat idea")[:80] + ".txt"
