"""A photo of the printed page a person prayed with, their pen marks and all.

A vision model reads it: the handwriting is copied word for word, and the printed words
they underlined, circled, bracketed or highlighted are listed. Each marked phrase that
can be found in the day's passage becomes one of their highlights (so it's marked in
the app too). The photo and a page of what was read are kept as a small PDF with that
day of the retreat, beside the journal. Nothing is written by the model except the
transcription: the marked words are the page's own, and they're only highlighted when
they match the passage."""

import difflib
import html
import json
import re
import struct
from datetime import datetime, timezone

import pymupdf

from . import config, jetstream, llm, pricing
from .extract import MAX_IMAGE_SIDE, ExtractError, _safe_pixmap

MAX_PDF_SIDE = 1600  # the photo in the PDF: sharp enough to read, small enough to keep
PDF_QUALITY = 72
FREE_VISION = "jetstream/llama-4-scout"

SYSTEM = """You read a photo of a printed prayer handout that a person has written and drawn on with a pen.
Report exactly what is there; never add, interpret or correct.

1. handwritten: every handwritten note, copied word for word as written (keep their spelling), each with the printed words it sits next to or points at ("near"). Ignore printed text.
2. marked: every piece of PRINTED text the person marked by underlining, circling, bracketing, boxing, starring or highlighting. Give the printed words exactly as printed (not the handwriting), and how it was marked. A mark that spans several lines is one entry with all its words, in reading order.
3. title: the page's printed heading, if any.

If something is unreadable, write [unclear] in its place. Reply with only JSON."""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "handwritten": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "near": {"type": "string"}}, "required": ["text", "near"], "additionalProperties": False}},
        "marked": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "how": {"type": "string"}}, "required": ["text", "how"], "additionalProperties": False}},
    },
    "required": ["title", "handwritten", "marked"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- the photo, upright

def _exif_rotation(data: bytes) -> int:
    """Degrees to turn a phone JPEG so it stands upright (its EXIF orientation), else 0."""
    if data[:2] != b"\xff\xd8":
        return 0
    i = 2
    while i + 4 < len(data):
        if data[i] != 0xFF:
            return 0
        marker, size = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker == 0xE1 and data[i + 4:i + 10] == b"Exif\x00\x00":
            tiff = data[i + 10:i + 2 + size]
            endian = "<" if tiff[:2] == b"II" else ">"
            first = struct.unpack(endian + "I", tiff[4:8])[0]
            count = struct.unpack(endian + "H", tiff[first:first + 2])[0]
            for k in range(count):
                entry = tiff[first + 2 + 12 * k: first + 14 + 12 * k]
                if struct.unpack(endian + "H", entry[:2])[0] == 0x0112:
                    value = struct.unpack(endian + "H", entry[8:10])[0]
                    return {3: 180, 6: 90, 8: 270}.get(value, 0)
            return 0
        i += 2 + size
    return 0


def upright_jpeg(data: bytes, longest: int, quality: int) -> tuple[bytes, int, int]:
    """The photo turned upright and scaled so its longest side is at most `longest`."""
    try:
        pix = _safe_pixmap(data)
    except ExtractError:
        raise
    except Exception as exc:
        raise ExtractError("That photo couldn't be read. Please use a JPEG or PNG.") from exc
    turn = _exif_rotation(data)
    w, h = (pix.height, pix.width) if turn in (90, 270) else (pix.width, pix.height)
    scale = min(1.0, longest / max(w, h))
    doc = pymupdf.open()
    page = doc.new_page(width=w * scale, height=h * scale)
    page.insert_image(page.rect, stream=data, rotate=turn)
    out = page.get_pixmap(dpi=72, alpha=False)
    return out.tobytes("jpeg", jpg_quality=quality), out.width, out.height


# ---------------------------------------------------------------- reading it

async def read(photo: bytes, model: str, meter: pricing.Meter) -> dict:
    """{"title", "handwritten": [{text, near}], "marked": [{text, how}]} from the photo."""
    jpeg, _, _ = upright_jpeg(photo, MAX_IMAGE_SIDE, 85)
    if config.LLM_MODE == "stub":
        return {"title": "", "handwritten": [], "marked": []}
    if pricing.is_jetstream(model):
        reply = await jetstream.complete(pricing.api_model(FREE_VISION), SYSTEM, "The photo:", meter,
                                         images=[(jpeg, "image/jpeg")], max_tokens=4000, whole=True)
    else:
        message = await llm._call(meter, system=SYSTEM, max_tokens=4000,
                                  messages=[{"role": "user", "content": [llm._image_block(jpeg, "image/jpeg"),
                                                                         {"type": "text", "text": "The photo:"}]}],
                                  output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
        reply = llm._text(message)
    match = re.search(r"\{.*\}", reply, re.S)
    found = json.loads(match.group(0)) if match else {}
    clean = lambda s: " ".join(str(s or "").split())[:600]  # noqa: E731
    return {"title": clean(found.get("title")),
            "handwritten": [{"text": clean(h.get("text")), "near": clean(h.get("near"))} for h in found.get("handwritten") or [] if clean(h.get("text"))][:40],
            "marked": [{"text": clean(llm.strip_verse_numbers(clean(m.get("text")))), "how": clean(m.get("how"))[:30]}  # printed verse numbers out
                       for m in found.get("marked") or [] if clean(m.get("text"))][:40]}


def in_passage(phrase: str, passage: str) -> str | None:
    """The passage's own words that a marked phrase matches (photos misread a letter or
    two), or None. Matched word by word, so the highlight is exactly the passage's text."""
    words = [(m.start(), m.end(), re.sub(r"\W", "", m.group(0)).lower()) for m in re.finditer(r"\S+", passage)]
    want = [re.sub(r"\W", "", w).lower() for w in phrase.split() if re.sub(r"\W", "", w)]
    if len(want) < 2 or not words:
        return None
    keys = [w[2] for w in words]
    best, where = 0.0, None
    for start in range(len(keys)):
        for size in {len(want) - 1, len(want), len(want) + 1}:
            if size < 1 or start + size > len(keys):
                continue
            ratio = difflib.SequenceMatcher(None, " ".join(keys[start:start + size]), " ".join(want)).ratio()  # letters, so one misread letter costs little
            if ratio > best:
                best, where = ratio, (start, start + size)
    if best < 0.75 or not where:
        return None
    a, b = where
    return " ".join(passage[words[a][0]:words[b - 1][1]].split())


# ---------------------------------------------------------------- the PDF

def make_pdf(photo: bytes, heading: str, when: str, found: dict) -> bytes:
    """The photo (upright, a sensible size), then a page of what was read from it."""
    jpeg, w, h = upright_jpeg(photo, MAX_PDF_SIDE, PDF_QUALITY)
    doc = pymupdf.open()
    width = 612  # US letter width, in points
    page = doc.new_page(width=width, height=width * h / w)
    page.insert_image(page.rect, stream=jpeg)
    esc = lambda t: html.escape(t)  # noqa: E731
    parts = [f"<h1>{esc(heading)}</h1>", f"<p class='when'>From the photo of your page, {esc(when)}</p>"]
    if found["handwritten"]:
        parts.append("<h2>Your handwritten notes</h2><ul>" + "".join(
            f"<li><b>{esc(n['text'])}</b>" + (f" <span>beside “{esc(n['near'])}”</span>" if n["near"] else "") + "</li>"
            for n in found["handwritten"]) + "</ul>")
    if found["marked"]:
        parts.append("<h2>What you marked on the page</h2><ul>" + "".join(
            f"<li>{esc(m['text'])}" + (f" <span>({esc(m['how'])})</span>" if m["how"] else "") + "</li>" for m in found["marked"]) + "</ul>")
    if not found["handwritten"] and not found["marked"]:
        parts.append("<p>No handwriting or marks were found on this photo.</p>")
    css = ("* { font-family: serif; color: #1c1712; } h1 { font-size: 17px; margin: 0 0 4px; } .when { font-size: 11px; color: #6b5d4c; margin: 0 0 16px; }"
           " h2 { font-size: 13px; color: #a32a1c; margin: 16px 0 6px; } li { font-size: 12px; margin: 0 0 7px; line-height: 1.45; } span { color: #6b5d4c; font-style: italic; }")
    notes = doc.new_page(width=612, height=792)
    notes.insert_htmlbox(pymupdf.Rect(54, 54, 612 - 54, 792 - 54), "".join(parts), css=css)
    return doc.tobytes(garbage=4, deflate=True)


def summary_heading(retreat: dict, day: dict) -> str:
    week = f"Week {len(retreat['series']) + 1} · " if retreat.get("series") else ""
    return f"{(retreat.get('plan') or {}).get('title', 'Retreat')} · {week}Day {day['day']} · {day.get('title', '')}".strip(" ·")


def now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["read", "in_passage", "make_pdf", "summary_heading", "ExtractError"]
