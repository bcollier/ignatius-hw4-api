import pymupdf

from app.extract import extract
from app.pipeline import fit
from app.tts import chunk_text


def test_chunk_text_respects_limit_and_keeps_words():
    text = " ".join(f"Sentence number {i} is here." for i in range(200))
    pieces = chunk_text(text, 300)
    assert all(len(p) <= 300 for p in pieces)
    assert " ".join(pieces).split() == text.split()


def test_fit_trims_at_sentence_boundary():
    text = "One sentence here. " * 50
    trimmed, was_trimmed = fit(text, 100)
    assert was_trimmed and len(trimmed) <= 100 and trimmed.endswith(".")
    assert fit("Short.", 100) == ("Short.", False)


def test_scanned_page_is_rendered_for_ocr():
    # Make a page that is only a picture of text, like a scan.
    src = pymupdf.open()
    src.new_page().insert_text((72, 72), "The Lord is my shepherd", fontsize=24)
    png = src[0].get_pixmap(dpi=100).tobytes("png")
    scan = pymupdf.open()
    scan.new_page().insert_image(scan[0].rect, stream=png)
    result = extract("scan.pdf", scan.tobytes())
    assert result.text == "" and len(result.scanned_pages) == 1
