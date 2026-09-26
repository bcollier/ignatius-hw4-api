"""Build the demo retreat package: "Come and See: Seven Encounters with Jesus".

Seven days, each with a World English Bible passage (public domain, via
bible-api.com), a grace to ask for, and a public-domain painting from Wikimedia
Commons at high resolution. Produces come-and-see.pdf, the source document the two
demo retreats are made from (free: Muse and Microsoft voices; premium: Claude
Fable 5.1 and ElevenLabs).

Run: .venv/bin/python samples/demo/make_demo.py
"""

import io
import json
import urllib.parse
import urllib.request
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
IMG = HERE / "images"
UA = {"User-Agent": "IgnatiusAtHomeHW/0.1 (student project; https://github.com/bcollier/ignatius-hw4-api)"}

DAYS = [
    {
        "title": "Follow Me",
        "ref": "Mark 1:16-20",
        "grace": "Ask for the grace to hear Jesus call me where I am, and to leave behind whatever keeps me from following him.",
        "file": "File:Duccio di Buoninsegna, The Calling of the Apostles Peter and Andrew, 1308-1311, NGA 282.jpg",
        "credit": "Duccio di Buoninsegna, The Calling of the Apostles Peter and Andrew, 1308-1311. National Gallery of Art, Washington.",
    },
    {
        "title": "He Rose and Followed",
        "ref": "Matthew 9:9-13",
        "grace": "Ask for the grace to know that Jesus calls me as I am, with all my history, and to rise and follow him.",
        "file": "File:Caravaggio — The Calling of Saint Matthew.jpg",
        "credit": "Caravaggio, The Calling of Saint Matthew, 1599-1600. San Luigi dei Francesi, Rome.",
    },
    {
        "title": "Living Water",
        "ref": "John 4:7-15",
        "grace": "Ask for the grace to recognize my deepest thirst, and to receive the living water Jesus offers.",
        "file": "File:Carl Heinrich Bloch - Woman at the Well.jpg",
        "credit": "Carl Bloch, Woman at the Well, 19th century.",
    },
    {
        "title": "Why Are You Afraid?",
        "ref": "Mark 4:35-41",
        "grace": "Ask for the grace to trust Jesus in the storms of my life, even when he seems to be asleep.",
        "file": "File:Rembrandt Christ in the Storm on the Lake of Galilee.jpg",
        "credit": "Rembrandt, Christ in the Storm on the Sea of Galilee, 1633.",
    },
    {
        "title": "Welcomed Home",
        "ref": "Luke 15:17-24",
        "grace": "Ask for the grace to come home, and to let the Father embrace me before I finish my apology.",
        "file": "File:Rembrandt Harmensz van Rijn - Return of the Prodigal Son - Google Art Project.jpg",
        "credit": "Rembrandt, The Return of the Prodigal Son, about 1668. State Hermitage Museum, Saint Petersburg.",
    },
    {
        "title": "Called by Name",
        "ref": "John 20:11-18",
        "grace": "Ask for the grace to hear the risen Jesus call me by name, and to go and tell what I have seen.",
        "file": "File:Titian - Noli me Tangere - Google Art Project.jpg",
        "credit": "Titian, Noli me Tangere, about 1514. National Gallery, London.",
    },
    {
        "title": "Our Hearts Burning",
        "ref": "Luke 24:28-35",
        "grace": "Ask for the grace to recognize Jesus in the breaking of the bread, and to feel my heart burn within me.",
        "file": "File:1602-3 Caravaggio,Supper at Emmaus National Gallery, London.jpg",
        "credit": "Caravaggio, Supper at Emmaus, 1601. National Gallery, London.",
    },
]

INTRO = """This week is a small retreat in daily life: seven times of prayer, one a day, each with a moment when someone meets Jesus in the Gospels. Two fishermen at their nets. A tax collector at his table. A woman at a well at noon. Frightened friends in a boat. A son on the road home. A woman weeping at a tomb. Two travelers at supper.

Each day, find a quiet place and about half an hour. Become aware that God is already present. Ask for the grace named for the day, plainly, as something you want. Read the passage slowly, more than once. Let the painting help you enter the scene: where are you standing, what do you see and hear, who looks at you? Notice what stirs in you, peace or resistance, desire or fear, and stay with the word or image that holds you. End by speaking to Jesus in your own words, as one friend speaks to another, and close with the Our Father.

On the seventh day, return to the day that moved you most."""

CSS = """
body { font-family: serif; color: #2b2620; }
h1 { font-size: 30pt; margin: 0 0 4pt 0; }
h2 { font-size: 20pt; margin: 0 0 2pt 0; }
.sub { font-family: sans-serif; font-size: 11pt; color: #7a4b2a; letter-spacing: 1pt; }
.ref { font-family: sans-serif; font-size: 10pt; color: #6d655b; margin: 0 0 8pt 0; }
.grace { font-style: italic; font-size: 12pt; margin: 6pt 0 10pt 0; }
.passage { font-size: 12pt; line-height: 1.5; }
.credit { font-family: sans-serif; font-size: 8pt; color: #6d655b; margin-top: 4pt; }
p { margin: 0 0 8pt 0; line-height: 1.45; font-size: 11.5pt; }
"""


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def image(day: int, commons_title: str) -> Path:
    path = IMG / f"day{day}.jpg"
    if path.exists():
        return path
    q = urllib.parse.urlencode({"action": "query", "titles": commons_title, "prop": "imageinfo", "iiprop": "url",
                                "iiurlwidth": 1920, "format": "json"})
    page = next(iter(json.loads(fetch(f"https://commons.wikimedia.org/w/api.php?{q}"))["query"]["pages"].values()))
    path.write_bytes(fetch(page["imageinfo"][0]["thumburl"]))
    return path


def passage(ref: str) -> str:
    path = HERE / "text" / (ref.replace(" ", "_").replace(":", "_") + ".txt")
    if not path.exists():
        data = json.loads(fetch(f"https://bible-api.com/{urllib.parse.quote(ref)}?translation=web"))
        path.write_text(" ".join(v["text"].strip() for v in data["verses"]))
    return path.read_text()


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build() -> Path:
    IMG.mkdir(exist_ok=True)
    (HERE / "text").mkdir(exist_ok=True)
    archive = pymupdf.Archive()
    sections = []
    intro = "".join(f"<p>{esc(p)}</p>" for p in INTRO.split("\n\n"))
    days = "".join(f"<p>Day {n}. {esc(d['title'])} · {esc(d['ref'])}</p>" for n, d in enumerate(DAYS, start=1))
    # A typographic cover: each painting appears once, on its own day, so each day gets its image.
    sections.append(
        '<p class="sub">A SEVEN-DAY RETREAT IN DAILY LIFE</p><h1>Come and See</h1>'
        '<p class="ref">Seven encounters with Jesus · World English Bible (public domain) · public-domain paintings</p>'
        f'<div style="margin-top: 36pt">{days}</div>'
    )
    sections.append(f"<h2>How to pray this week</h2>{intro}")
    for n, d in enumerate(DAYS, start=1):
        img = image(n, d["file"])
        name = f"day{n}.jpg"
        archive.add(img.read_bytes(), name)
        sections.append(
            f'<p class="sub">DAY {n}</p><h2>Day {n}: {esc(d["title"])}</h2><p class="ref">{esc(d["ref"])}</p>'
            f'<p class="grace">Grace: {esc(d["grace"])}</p><p class="passage">{esc(passage(d["ref"]))}</p>'
            f'<img src="{name}" width="440"/><p class="credit">{esc(d["credit"])} Public domain, via Wikimedia Commons.</p>'
        )
    credits = "".join(f"<p>Day {n}: {esc(d['credit'])}</p>" for n, d in enumerate(DAYS, start=1))
    sections.append("<h2>Credits</h2><p>Scripture: World English Bible, public domain.</p>" + credits +
                    "<p>Images: public domain or CC0, from Wikimedia Commons.</p>")

    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    rect = pymupdf.paper_rect("letter")
    for html in sections:
        story = pymupdf.Story(html=html, user_css=CSS, archive=archive)
        more = True
        while more:
            dev = writer.begin_page(rect)
            more, _ = story.place(rect + (54, 54, -54, -54))
            story.draw(dev)
            writer.end_page()
    writer.close()
    out = HERE / "come-and-see.pdf"
    doc = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
    doc.save(out, garbage=3, deflate=True)
    return out


if __name__ == "__main__":
    out = build()
    print(out, round(out.stat().st_size / 1e6, 1), "MB", pymupdf.open(out).page_count, "pages")
