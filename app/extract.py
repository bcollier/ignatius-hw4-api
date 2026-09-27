"""Pull text and images out of an uploaded PDF, Word document, text file or photo."""

import io
from dataclasses import dataclass, field

import docx
import pymupdf

from . import config

MIN_IMAGE_SIDE = 80  # skip bullets, rules and other decoration
MAX_IMAGE_SIDE = 1568  # larger images are scaled down before storage and vision calls


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


def photo_image(data: bytes) -> Image:
    """A photo, sized and encoded like a scanned page, for a vision model to read."""
    try:
        pix = pymupdf.Pixmap(data)
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
                pix = page.get_pixmap(dpi=150)
                img = _to_web_image(pix, page_no, fmt="png")
                if img:
                    scanned.append(img)
            continue
        if page_text:
            texts.append(f"[Page {page_no}]\n{page_text}")

        for info in page_images:
            xref = info[0]
            if xref in seen or len(images) >= config.MAX_IMAGES:
                continue
            seen.add(xref)
            try:
                img = _to_web_image(pymupdf.Pixmap(doc, xref), page_no)
            except Exception:
                continue  # unusual encodings are skipped rather than failing the upload
            if img:
                images.append(img)

    return Extracted(kind="pdf", text="\n\n".join(texts), page_count=doc.page_count, images=images, scanned_pages=scanned)


def _extract_docx(data: bytes) -> Extracted:
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
            img = _to_web_image(pymupdf.Pixmap(rel.target_part.blob), None)
        except Exception:
            continue
        if img:
            images.append(img)

    return Extracted(kind="docx", text="\n\n".join(parts), page_count=0, images=images)
