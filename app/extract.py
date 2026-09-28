"""Pull text and images out of an uploaded PDF, Word document, text file or photo."""

import io
import math
import zipfile
from dataclasses import dataclass, field

import docx
import pymupdf

from . import config

MIN_IMAGE_SIDE = 80  # skip bullets, rules and other decoration
MAX_IMAGE_SIDE = 1568  # larger images are scaled down before storage and vision calls
# Limits checked before anything is decoded or expanded, so a small file can't turn into
# a huge one in memory (an image bomb, a zip bomb, a giant page drawn at full resolution).
MAX_PIXELS = 40_000_000  # an image's width x height, before it's decoded
MAX_RENDER_PIXELS = 12_000_000  # a scanned page, as drawn
MAX_DOCX_ENTRIES = 5_000
MAX_DOCX_EXPANDED = 100 * 1024 * 1024  # all the parts of a Word file, unpacked
MAX_DOCX_RATIO = 200  # a part that unpacks to more than 200 times its size is refused


PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff")


class ExtractError(ValueError):
    """The upload can't be used; the message is safe to show to the user."""


@dataclass
class Image:
    data: bytes
    mime: str
    width: int
    height: int
    page: int | None = None  # 1-based page number for PDFs


@dataclass
class Extracted:
    kind: str  # "pdf" or "docx"
    text: str
    page_count: int
    images: list[Image] = field(default_factory=list)
    # Pages with no text layer, rendered as PNG so a vision model can read them.
    scanned_pages: list[Image] = field(default_factory=list)
    truncated: bool = False


def extract(filename: str, data: bytes) -> Extracted:
    name = filename.lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        result = _extract_pdf(data)
    elif name.endswith(".docx"):
        result = _extract_docx(data)
    elif name.endswith((".txt", ".md", ".markdown")):
        result = _extract_text(data)
    elif name.endswith(PHOTO_SUFFIXES):
        result = extract_photo(data)
    else:
        raise ExtractError("Upload a PDF (.pdf), Word document (.docx), text file (.txt) or photo (.jpg or .png).")

    if len(result.text) > config.MAX_SOURCE_CHARS:
        result.text = result.text[: config.MAX_SOURCE_CHARS]
        result.truncated = True
    if not result.text.strip() and not result.scanned_pages:
        raise ExtractError("No text found in this document.")
    return result


def _extract_text(data: bytes) -> Extracted:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    return Extracted(text=text.replace("\r\n", "\n"), images=[], kind="text", page_count=0)


def _image_size(data: bytes) -> tuple[int, int]:
    """An image's width and height from its header, without decoding the pixels."""
    with pymupdf.open(stream=data) as doc:
        rect = doc[0].rect
        return int(rect.width), int(rect.height)


def _safe_pixmap(data: bytes) -> pymupdf.Pixmap:
    width, height = _image_size(data)
    if width * height > MAX_PIXELS:
        raise ExtractError("That image is too large to read (over 40 megapixels).")
    return pymupdf.Pixmap(data)


def photo_image(data: bytes) -> Image:
    """A photo, sized and encoded like a scanned page, for a vision model to read."""
    try:
        pix = _safe_pixmap(data)
    except ExtractError:
        raise
    except Exception as exc:
        raise ExtractError("That photo couldn't be read. Please use a JPEG or PNG.") from exc
    img = _to_web_image(pix, 1, fmt="jpeg")
    if not img:
        raise ExtractError("That photo is too small to read.")
    return img


def extract_photo(data: bytes) -> Extracted:
    """A photo of a page, a book or a card: the planner reads it like a scanned page."""
    return Extracted(kind="photo", text="", page_count=1, scanned_pages=[photo_image(data)])


def _to_web_image(pix: pymupdf.Pixmap, page: int | None, fmt: str = "jpeg") -> Image | None:
    """Normalize to RGB, cap the size, and encode as JPEG (photos) or PNG (scanned text)."""
    if pix.width < MIN_IMAGE_SIDE or pix.height < MIN_IMAGE_SIDE:
        return None
    if pix.alpha or pix.colorspace is None or pix.colorspace.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)  # drop alpha, convert CMYK and others to RGB
    longest = max(pix.width, pix.height)
    if longest > MAX_IMAGE_SIDE:  # scale down just enough, keeping as much detail as allowed
        scale = MAX_IMAGE_SIDE / longest
        pix = pymupdf.Pixmap(pix, max(1, round(pix.width * scale)), max(1, round(pix.height * scale)))
    data = pix.tobytes("jpeg", jpg_quality=85) if fmt == "jpeg" else pix.tobytes("png")
    return Image(data=data, mime=f"image/{fmt}", width=pix.width, height=pix.height, page=page)


def _extract_pdf(data: bytes) -> Extracted:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ExtractError("This file isn't a readable PDF.") from exc
    if doc.needs_pass:
        raise ExtractError("This PDF is password protected.")
    if doc.page_count > config.MAX_PAGES:
        raise ExtractError(f"This PDF has {doc.page_count} pages; the limit is {config.MAX_PAGES}.")

    texts, images, scanned, seen = [], [], [], set()
    for page in doc:
        page_no = page.number + 1
        page_text = page.get_text("text").replace("\f", "\n").strip()
        page_images = page.get_images(full=True)

        if len(page_text) < 20 and page_images:
            if len(scanned) < config.MAX_SCANNED_PAGES:
                pix = page.get_pixmap(dpi=_render_dpi(page))
                img = _to_web_image(pix, page_no, fmt="png")
                if img:
                    scanned.append(img)
            continue
        if page_text:
            texts.append(f"[Page {page_no}]\n{page_text}")

        for info in page_images:
            xref, width, height = info[0], info[2], info[3]
            if xref in seen or len(images) >= config.MAX_IMAGES or width * height > MAX_PIXELS:
                continue
            seen.add(xref)
            try:
                img = _to_web_image(pymupdf.Pixmap(doc, xref), page_no)
            except Exception:
                continue  # unusual encodings are skipped rather than failing the upload
            if img:
                images.append(img)

    return Extracted(kind="pdf", text="\n\n".join(texts), page_count=doc.page_count, images=images, scanned_pages=scanned)


def _render_dpi(page) -> int:
    """150 dpi, or less for a very large page, so the drawing stays under MAX_RENDER_PIXELS."""
    points = max(1.0, page.rect.width * page.rect.height)
    return max(36, min(150, int(72 * math.sqrt(MAX_RENDER_PIXELS / points))))


def _check_docx_archive(data: bytes) -> None:
    """A Word file is a zip archive: refuse one that would unpack to something enormous."""
    try:
        entries = zipfile.ZipFile(io.BytesIO(data)).infolist()
    except zipfile.BadZipFile as exc:
        raise ExtractError("This file isn't a readable Word document.") from exc
    if len(entries) > MAX_DOCX_ENTRIES or sum(e.file_size for e in entries) > MAX_DOCX_EXPANDED:
        raise ExtractError("This Word document unpacks to more than the app can read.")
    if any(e.file_size > 1024 * 1024 and e.file_size > MAX_DOCX_RATIO * max(1, e.compress_size) for e in entries):
        raise ExtractError("This Word document is compressed in a way the app won't open.")


def _extract_docx(data: bytes) -> Extracted:
    _check_docx_archive(data)
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ExtractError("This file isn't a readable Word document.") from exc

    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))

    images = []
    for rel in document.part.rels.values():
        if "image" not in rel.reltype or rel.is_external or len(images) >= config.MAX_IMAGES:
            continue
        try:
            img = _to_web_image(_safe_pixmap(rel.target_part.blob), None)
        except Exception:
            continue
        if img:
            images.append(img)

    return Extracted(kind="docx", text="\n\n".join(parts), page_count=0, images=images)
