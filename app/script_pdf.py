"""A printable script: each day laid out in the same order the player uses, so
someone can follow along on paper or a tablet. Built with PyMuPDF's HTML layout."""

import html
import io
from datetime import datetime

import pymupdf

from . import prompts

PAGE = pymupdf.paper_rect("letter")
MARGIN = 60
CSS = """
body { font-family: serif; font-size: 11.5pt; line-height: 1.45; color: #222; }
h1 { font-size: 22pt; margin: 0 0 6pt 0; }
h2 { font-size: 17pt; margin: 0 0 4pt 0; }
h3 { font-size: 10pt; font-family: sans-serif; color: #7a4b2a; margin: 16pt 0 3pt 0; text-transform: uppercase; }
p { margin: 0 0 7pt 0; }
.meta { font-family: sans-serif; font-size: 9pt; color: #666; }
.grace { font-style: italic; }
.cue { font-style: italic; color: #444; }
.silence { font-family: sans-serif; font-size: 9pt; color: #7a4b2a; text-align: center; margin: 10pt 0; }
.reading { margin-left: 14pt; }
.sources { font-family: sans-serif; font-size: 8.5pt; color: #555; }
.toc { font-family: sans-serif; font-size: 10.5pt; }
"""


def _paragraphs(text: str, cls: str = "") -> str:
    attr = f' class="{cls}"' if cls else ""
    parts = [p.strip() for p in text.replace("\r", "").split("\n\n") if p.strip()]
    return "".join(f"<p{attr}>{html.escape(p).replace(chr(10), '<br/>')}</p>" for p in parts)


def _seconds_text(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = seconds // 60
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def day_html(retreat: dict, day: dict, state: dict, order: str, grace_silence: int, pause: int, image_name: str | None) -> str:
    """One day, in prayer order. Mirrors buildSequence() in the web app's app.js."""
    guide = {k: c for k, c in (state or {}).get("guide", {}).items() if c.get("status") == "ready"}
    tracks = {k: t for k, t in (state or {}).get("tracks", {}).items() if t.get("status") == "ready"}
    out = [f"<h2>Day {day['day']}: {html.escape(day['title'])}</h2>"]
    if day.get("source_ref"):
        out.append(f'<p class="meta">{html.escape(day["source_ref"])}</p>')
    if image_name:
        out.append(f'<p><img src="{image_name}" width="220"/></p>')
    if day.get("grace"):
        out.append(f'<p class="grace">Grace: {html.escape(day["grace"])}</p>')

    passage = tracks.get("reading", {}).get("script") or day["passage_text"]

    def cue(name: str) -> None:
        if name in guide:
            out.append(f"<h3>{html.escape(prompts.GUIDE_LABELS.get(name, name))}</h3>")
            out.append(_paragraphs(guide[name]["script"], "cue"))

    def reading(label: str) -> None:
        out.append(f"<h3>{label}</h3>")
        out.append(_paragraphs(passage, "reading"))

    def section(key: str, label: str) -> None:
        track = tracks.get(key)
        if not track:
            return
        out.append(f"<h3>{label}</h3>")
        out.append(_paragraphs(track["script"]))
        if track.get("sources"):
            note = "Sources checked with web search" if track.get("web_search") else "Sources suggested by the model (not checked)"
            items = "".join(f"<li>{html.escape(s)}</li>" for s in track["sources"])
            out.append(f'<div class="sources"><p>{note}:</p><ul>{items}</ul></div>')

    if not state or state.get("status") == "idle":
        # Not built yet: the passage and grace are still useful for prayer.
        reading("The reading")
        out.append('<p class="meta">The reflection and deep dive for this day haven\'t been written yet.</p>')
        return "".join(out)

    cue("opening")
    if "opening" in guide:
        out.append(f'<p class="silence">· Silence, {_seconds_text(grace_silence)} ·</p>')
    if order == "lectio":
        cue("first")
        reading("First reading")
        section("heart", "For the heart")
        cue("second")
        reading("Second reading")
        section("deep", "Deep dive")
        cue("third")
        reading("Third reading")
    else:
        reading("The reading")
        section("heart", "For the heart")
        section("deep", "Deep dive")
    cue("silence")
    out.append(f'<p class="silence">· Bell · silence, {_seconds_text(pause)} · Bell ·</p>')
    if order == "lectio":
        cue("last")
        reading("Last reading")
    cue("closing")
    journal = (state or {}).get("journal") or {}
    if (state or {}).get("prayed_at") or journal:
        out.append("<h3>After praying</h3>")
        if state.get("prayed_at"):
            out.append(f'<p class="meta">Prayed {html.escape(_date(state["prayed_at"]))}</p>')
        if journal.get("word"):
            out.append(f'<p class="grace">The word that stayed: {html.escape(journal["word"])}</p>')
        if journal.get("note"):
            out.append(_paragraphs(journal["note"]))
    return "".join(out)


def _date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%A, %B %-d, %Y")
    except ValueError:
        return iso[:10]


def cover_html(retreat: dict, days: list[dict], series_titles: list[str] | None = None) -> str:
    plan = retreat["plan"]
    toc = "".join(f"<li>Day {d['day']}: {html.escape(d['title'])}</li>" for d in days)
    series = ""
    if series_titles:
        earlier = "".join(f"<li>Week {i}: {html.escape(t)}</li>" for i, t in enumerate(series_titles, start=1))
        series = (f'<p class="meta">Week {len(series_titles) + 1} of a series. Earlier weeks:</p>'
                  f'<div class="toc"><ul>{earlier}</ul></div>')
    return (
        f"<h1>{html.escape(plan['title'])}</h1>"
        f"{_paragraphs(plan.get('summary', ''))}"
        f'<p class="meta">From {html.escape(retreat["filename"])} · Ignatius at Home</p>'
        f"{series}"
        f'<div class="toc"><ul>{toc}</ul></div>'
    )


def render(sections: list[str], images: dict[str, bytes]) -> bytes:
    """Each section starts on a new page."""
    archive = pymupdf.Archive()
    for name, data in images.items():
        archive.add(data, name)
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    where = PAGE + (MARGIN, MARGIN, -MARGIN, -MARGIN)
    for body in sections:
        story = pymupdf.Story(html=body, user_css=CSS, archive=archive)
        more = True
        while more:
            device = writer.begin_page(PAGE)
            more, _ = story.place(where)
            story.draw(device)
            writer.end_page()
    writer.close()
    return _number_pages(buffer.getvalue())


def _number_pages(data: bytes) -> bytes:
    doc = pymupdf.open(stream=data, filetype="pdf")
    for page in doc:
        page.insert_text((PAGE.width / 2 - 8, PAGE.height - 30), str(page.number + 1), fontsize=8, fontname="helv", color=(0.5, 0.5, 0.5))
    return doc.tobytes(garbage=3, deflate=True)
