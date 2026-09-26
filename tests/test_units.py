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


def test_free_voice_pieces_are_retried(monkeypatch):
    import asyncio

    from app import tts

    calls = {"n": 0}

    class Flaky:
        def __init__(self, *a, **k):
            pass

        async def stream(self):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("dropped")
            yield {"type": "audio", "data": b"mp3"}
            yield {"type": "WordBoundary", "offset": 5_000_000, "text": "hello"}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(tts.edge_tts, "Communicate", Flaky)
    monkeypatch.setattr(tts.asyncio, "sleep", no_sleep)
    assert asyncio.run(tts._edge_piece("hello", "v")) == (b"mp3", [(0.5, "hello")]) and calls["n"] == 3


def test_word_timings_align_to_the_script():
    from app import tts

    text = 'Jesus said to her, "Mary." She turned.'
    spoken = [(0.1, "Jesus"), (0.4, "said"), (0.6, "to"), (0.7, "her"), (1.0, "Mary"), (1.6, "She"), (1.8, "turned")]
    words = tts.align(text, spoken)
    assert [i for _, i in words] == [0, 6, 11, 14, 20, 27, 31]
    assert tts.align("one two", [(0, "zzz"), (0.5, "two")]) == [[0.5, 4]]  # an unmatched word is skipped
    alignment = {"characters": list("Hi there"), "character_start_times_seconds": [0, .1, .2, .3, .4, .5, .6, .7]}
    assert tts.words_from_alignment(alignment, 10.0) == [(10.0, "Hi"), (10.3, "there")]


def test_writers_know_the_rest_of_the_retreat():
    from app import prompts

    plan = {"days": [{"day": n, "title": f"T{n}", "source_ref": f"Ref {n}", "passage_text": f"passage {n}"} for n in (1, 2, 3, 4)]}
    days = {"1": {"tracks": {"heart": {"script": "HEART ONE"}, "deep": {"script": "DEEP ONE"}}},
            "2": {"tracks": {"heart": {"script": "HEART TWO " * 50}, "deep": {"script": "DEEP TWO"}}},
            "4": {"tracks": {"heart": {"script": "FUTURE ANALYSIS"}}}}
    text = prompts.retreat_so_far(plan, days, 3)
    assert "Today is Day 3 of 4" in text and "Don't explain again" in text
    assert text.index("Day 2: T2") < text.index("Day 1: T1")  # most recent first
    assert "DEEP ONE" in text and "passage 4" in text and "FUTURE ANALYSIS" not in text  # coming days: readings only
    tight = prompts.retreat_so_far(plan, days, 3, max_chars=100)
    assert "HEART TWO" not in tight and "HEART ONE" in tight  # too long: title only, older short day still fits
    assert prompts.retreat_so_far({"days": plan["days"][:1]}, {}, 1) == ""


def test_passage_is_copied_between_its_first_and_last_words():
    from app.llm import passage_between

    text = ("Day 3\n[Page 2]\nPsalm 33\nRejoice in Yahweh, you righteous!\nPraise is fitting for the upright.\n"
            "Our soul has waited for Yahweh.\nHe is our help and our shield.\n\nDay 4 Jeremiah 18")
    got = passage_between(text, "Rejoice in Yahweh, you righteous! Praise is", "waited for Yahweh. He is our help and our shield")
    assert got.startswith("Rejoice in Yahweh") and got.endswith("our shield.") and "Day 4" not in got
    assert passage_between(text, "words that are not there at all", "our shield") == ""


def test_a_psalm_that_ends_with_its_first_line_is_copied_whole():
    from app.llm import passage_between

    text = ("Day 1\nO LORD, our Lord, How majestic is Your name in all the earth!\nWhat is man that You take thought of him?\n"
            "O LORD, our Lord, How majestic is Your name in all the earth!\n\nDay 2\nBless the LORD, O my soul!")
    stop = text.index("Day 2")
    got = passage_between(text, "O LORD, our Lord, How majestic is Your name", "How majestic is Your name in all the earth!", stop)
    assert "What is man" in got and got.count("How majestic") == 2 and "Bless" not in got
