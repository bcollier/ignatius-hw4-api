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


def test_grace_is_spoken_once_whatever_its_form():
    from app.prompts import GUIDE_DEFAULTS, guide_text

    day = {"day": 2, "title": "Come to Me", "grace": "Ask for the grace to come to him just as I am."}
    opening = guide_text(GUIDE_DEFAULTS["opening"], day)
    assert opening.count("Ask for") == 1 and "{" not in opening
    day["grace"] = "To know God's closeness"
    assert "Ask for this grace: to know God's closeness." in guide_text(GUIDE_DEFAULTS["opening"], day)


def test_day_number_is_not_repeated_in_the_opening():
    from app.prompts import GUIDE_DEFAULTS, guide_text

    day = {"day": 1, "title": "Day 1: Isaiah 43:1-4", "grace": ""}
    assert guide_text(GUIDE_DEFAULTS["opening"], day).startswith("Day 1. Isaiah 43:1-4. Settle")


def test_meter_prices_tokens_cache_and_searches():
    from types import SimpleNamespace

    from app.pricing import FALLBACK_PRICES, Meter

    meter = Meter("anthropic/claude-opus-5", FALLBACK_PRICES)
    usage = SimpleNamespace(
        input_tokens=10_000, output_tokens=2_000, cache_read_input_tokens=0, cache_creation_input_tokens=0,
        server_tool_use=SimpleNamespace(web_search_requests=3),
    )
    meter.add(usage)
    # 10k in at $5/M + 2k out at $25/M + 3 searches at $0.01
    assert meter.summary()["usd"] == round(0.05 + 0.05 + 0.03, 4)
    assert meter.summary()["web_searches"] == 3


def test_title_that_is_only_the_day_number():
    from app.prompts import GUIDE_DEFAULTS, guide_text

    text = guide_text(GUIDE_DEFAULTS["opening"], {"day": 5, "title": "Day 5", "grace": ""})
    assert text.startswith("Day 5. Settle yourself")
