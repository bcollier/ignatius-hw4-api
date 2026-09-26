"""Build the example source documents offered on the New retreat page.

    be-still.pdf   "Be Still: Five Days with the Psalms of Trust"
    blessed.pdf    "Blessed: Six Days with the Beatitudes"

Each is a small, finished retreat source: a cover, a page on how to pray, one page
per day (passage, grace, focus, painting) and a credits page. Scripture is the World
English Bible (public domain, via bible-api.com). Paintings are public domain or CC0
files from Wikimedia Commons, fetched at 1920 px wide; the script checks each file's
license on Commons before using it. Type is EB Garamond (SIL Open Font License).

Also writes examples.json (the list the app shows), a ~600 px cover.jpg per example,
and be-still.txt (the same retreat as plain text, to show that .txt uploads work).

Downloads are cached next to each example (images/, text/, and fonts/ here), so a
rebuild is offline once everything has been fetched.

Run: .venv/bin/python samples/examples/make_examples.py
"""

import io
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
FONTS = HERE / "fonts"
UA = {"User-Agent": "IgnatiusAtHomeHW/0.1 (student project; https://github.com/bcollier/ignatius-hw4-api)"}
FONT_URL = "https://raw.githubusercontent.com/octaviopardo/EBGaramond12/master/fonts/ttf/{}.ttf"
FONT_FILES = ["EBGaramond-Regular", "EBGaramond-Italic", "EBGaramond-SemiBold", "EBGaramond-SemiBoldItalic"]
OK_LICENSES = ("Public domain", "CC0", "PD")
NUMBERS = ["One", "Two", "Three", "Four", "Five", "Six", "Seven"]

# ---------------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------------

EXAMPLES = [
    {
        "slug": "be-still",
        "title": "Be Still",
        "subtitle": "Five Days with the Psalms of Trust",
        "kicker": "A five-day retreat in daily life",
        "description": "Five of the Bible’s great songs of trust, from the Shepherd Psalm to “you have searched me and you know me,” "
                       "each paired with a painting to pray with. A gentle first retreat for anyone who is tired, anxious or simply wants to rest in God.",
        "palette": {"cover": (0.114, 0.165, 0.227), "accent": "#8a6a3b", "gold": (0.80, 0.68, 0.45), "ink": "#262320"},
        "intro": [
            "The Psalms are the prayer book Jesus himself prayed. Among them is a small family of songs of trust: "
            "prayers for people who are frightened, worn out, far from home, or simply unsure that anyone is watching over them. "
            "They do not pretend that life is calm. The waters roar, the valley is dark, the night comes. "
            "But in the middle of it, someone says: you are with me.",
            "For the next five days, spend one time of prayer a day with one of these psalms. "
            "About twenty to thirty minutes is enough. Let each painting slow you down and help you enter the psalm with your imagination.",
        ],
        "steps": [
            ("Become still", "Find a quiet place. Sit comfortably, breathe slowly, and become aware that God is already present, looking at you with love."),
            ("Ask for the grace", "Each day names a grace. Ask for it plainly, as something you really want. Desire is where prayer begins."),
            ("Read slowly", "Read the psalm aloud if you can, and then read it again. Stay with any word or line that holds you; there is no need to finish."),
            ("Look", "Spend a few minutes with the painting. Where are you in it? What do you notice, and what do you feel as you look?"),
            ("Speak as a friend", "End by talking with God in your own words, as one friend speaks to another. Close with the Our Father."),
            ("Notice", "Afterwards, take a moment to notice what stirred: consolation or desolation, peace or resistance. Jot down a word if it helps."),
        ],
        "closing": "On the last day, or on a sixth day if you can, return to the psalm that moved you most and pray it again.",
        "days": [
            {
                "title": "The Lord Is My Shepherd",
                "ref": "Psalm 23",
                "grace": "Ask for the grace to know, deep down, that I am shepherded, and that with God I lack nothing I truly need.",
                "focus": "Let the Shepherd lead you beside still waters, and notice where in your life you are being invited to lie down and rest.",
                "file": "File:BuenPastorMurillo1660.jpg",
                "artist": "Bartolomé Esteban Murillo (1617–1682)",
                "work": "The Good Shepherd",
                "date": "c. 1660",
                "collection": "Museo del Prado, Madrid",
            },
            {
                "title": "Be Still, and Know",
                "ref": "Psalm 46:1-11",
                "grace": "Ask for the grace to be still in the midst of upheaval, and to know that God is God, and that I do not have to be.",
                "focus": "Name the waters that are roaring in your life right now, and listen beneath them for the voice that says, “Be still.”",
                "file": "File:Hovhannes Aivazovsky - The Ninth Wave - Google Art Project.jpg",
                "artist": "Ivan Aivazovsky (1817–1900)",
                "work": "The Ninth Wave",
                "date": "1850",
                "collection": "State Russian Museum, Saint Petersburg",
            },
            {
                "title": "I Lift Up My Eyes",
                "ref": "Psalm 121",
                "grace": "Ask for the grace to lift my eyes from my worries, and to trust the One who keeps me and never slumbers or sleeps.",
                "focus": "Offer God every going out and coming in of your day, and let him keep watch over each one.",
                "file": "File:Caspar David Friedrich, Morgen im Riesengebirge.jpg",
                "artist": "Caspar David Friedrich (1774–1840)",
                "work": "Morning in the Riesengebirge",
                "date": "1810–1811",
                "collection": "Charlottenburg Palace, Berlin (Prussian Palaces and Gardens Foundation)",
            },
            {
                "title": "Like a Weaned Child",
                "ref": "Psalm 131",
                "grace": "Ask for the grace of a quieted soul, content to rest in God like a child held by its mother.",
                "focus": "Set down one great matter that is too wonderful for you, and simply let yourself be held.",
                "file": "File:Tempi Madonna - Raphael.jpg",
                "artist": "Raphael (1483–1520)",
                "work": "The Tempi Madonna",
                "date": "1508",
                "collection": "Alte Pinakothek, Munich",
            },
            {
                "title": "You Have Searched Me",
                "ref": "Psalm 139:1-12",
                "grace": "Ask for the grace to know that I am fully known and fully loved, and that there is nowhere I could go beyond God’s reach.",
                "focus": "Bring God the place in you that feels darkest, and hear that even the darkness is not dark to him.",
                "file": "File:Van Gogh - Starry Night - Google Art Project.jpg",
                "artist": "Vincent van Gogh (1853–1890)",
                "work": "The Starry Night",
                "date": "1889",
                "collection": "Museum of Modern Art, New York",
            },
        ],
    },
    {
        "slug": "blessed",
        "title": "Blessed",
        "subtitle": "Six Days with the Beatitudes",
        "kicker": "A six-day retreat in daily life",
        "description": "Sit on the mountain with the crowds and hear Jesus’ Beatitudes a few lines at a time, "
                       "from “blessed are the poor in spirit” to “rejoice and be glad,” with a great painting for each day.",
        "palette": {"cover": (0.235, 0.137, 0.118), "accent": "#8c4a32", "gold": (0.82, 0.66, 0.42), "ink": "#27211e"},
        "intro": [
            "The Beatitudes open the Sermon on the Mount, and they turn the world upside down. "
            "Jesus looks at the crowd that has followed him up the hillside, the poor, the grieving, the hungry, the ones who have been pushed aside, "
            "and he calls them blessed. Not someday, once their troubles are over, but now, as they are.",
            "For the next six days, spend one time of prayer a day with one or two of the Beatitudes. "
            "About twenty to thirty minutes is enough. Imagine yourself on the mountain, close enough to see his face as he speaks, "
            "and let each painting help you find your place in the crowd.",
        ],
        "steps": [
            ("Become still", "Find a quiet place. Sit comfortably, breathe slowly, and become aware that God is already present, looking at you with love."),
            ("Ask for the grace", "Each day names a grace. Ask for it plainly, as something you really want. Desire is where prayer begins."),
            ("Read slowly", "Read the passage aloud if you can, and then read it again. Stay with any word or phrase that holds you; there is no need to finish."),
            ("Enter the scene", "Use the painting and your imagination. Who is around you on the hillside? What does Jesus look like as he says these words to you?"),
            ("Speak as a friend", "End by talking with Jesus in your own words, as one friend speaks to another. Close with the Our Father."),
            ("Notice", "Afterwards, take a moment to notice what stirred: consolation or desolation, peace or resistance. Jot down a word if it helps."),
        ],
        "closing": "On the sixth day you will hear the Beatitudes whole again. Notice which one has become yours.",
        "days": [
            {
                "title": "The Poor in Spirit",
                "ref": "Matthew 5:1-3",
                "grace": "Ask for the grace to sit at Jesus’ feet on the mountain, and to know my need of God as a gift rather than a failure.",
                "focus": "Come empty-handed today: notice what you are clinging to, and let Jesus call your poverty blessed.",
                "file": "File:Bloch-SermonOnTheMount.jpg",
                "artist": "Carl Bloch (1834–1890)",
                "work": "The Sermon on the Mount",
                "date": "1877",
                "collection": "Museum of National History, Frederiksborg Castle, Hillerød",
            },
            {
                "title": "Those Who Mourn, the Gentle",
                "ref": "Matthew 5:4-5",
                "grace": "Ask for the grace to bring my grief honestly to Jesus and to receive his comfort, and for a gentle heart that does not need to grasp.",
                "focus": "Name one loss you are carrying, and let Jesus sit beside you in it without hurrying you.",
                "file": "File:Van Gogh - Trauernder alter Mann.jpeg",
                "artist": "Vincent van Gogh (1853–1890)",
                "work": "Sorrowing Old Man (“At Eternity’s Gate”)",
                "date": "1890",
                "collection": "Kröller-Müller Museum, Otterlo",
            },
            {
                "title": "Hunger and Mercy",
                "ref": "Matthew 5:6-7",
                "grace": "Ask for the grace to hunger for what is right as deeply as I hunger for bread, and to show others the mercy I have been shown.",
                "focus": "Notice who is gleaning at the edges of your field, and what mercy you might leave for them.",
                "file": "File:Jean-François Millet - Gleaners - Google Art Project.jpg",
                "artist": "Jean-François Millet (1814–1875)",
                "work": "The Gleaners",
                "date": "1857",
                "collection": "Musée d’Orsay, Paris",
            },
            {
                "title": "The Pure in Heart",
                "ref": "Matthew 5:8",
                "grace": "Ask for the grace of an undivided heart, open like Mary’s, so that I may see God in all things.",
                "focus": "Pray with Mary’s “yes,” and ask where God is quietly showing his face to you today.",
                "file": "File:La Anunciación, by Fra Angelico, from Prado in Google EarthFXD.jpg",
                "artist": "Fra Angelico (c. 1395–1455)",
                "work": "The Annunciation",
                "date": "c. 1425–1426",
                "collection": "Museo del Prado, Madrid",
            },
            {
                "title": "Peacemakers",
                "ref": "Matthew 5:9-10",
                "grace": "Ask for the grace to make peace where there is division, and for the courage to stay faithful when doing right costs me something.",
                "focus": "Like Stephen, who prayed for the people stoning him, pray today for someone who has wronged you.",
                "file": "File:Rembrandt Harmensz. van Rijn 150.jpg",
                "artist": "Rembrandt van Rijn (1606–1669)",
                "work": "The Stoning of Saint Stephen",
                "date": "1625",
                "collection": "Musée des Beaux-Arts de Lyon",
            },
            {
                "title": "Rejoice and Be Glad",
                "ref": "Matthew 5:11-12",
                "whole": "Matthew 5:1-12",
                "grace": "Ask for the grace to rejoice in belonging to Jesus, whatever it costs, and to see my life among the great company of the blessed.",
                "focus": "Hear all the Beatitudes once more, slowly, and rest in the one that has become yours.",
                "file": "File:Ghent Altarpiece - Adoration of the Mystic Lamb.jpg",
                "artist": "Jan van Eyck (c. 1390–1441) and Hubert van Eyck (d. 1426)",
                "work": "The Adoration of the Mystic Lamb (Ghent Altarpiece, central panel)",
                "date": "1432",
                "collection": "Saint Bavo’s Cathedral, Ghent",
            },
        ],
    },
]

# ---------------------------------------------------------------------------------
# Fetching (cached)
# ---------------------------------------------------------------------------------


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        return r.read()


def fonts() -> dict[str, bytes]:
    FONTS.mkdir(exist_ok=True)
    out = {}
    for name in FONT_FILES:
        path = FONTS / f"{name}.ttf"
        if not path.exists():
            path.write_bytes(fetch(FONT_URL.format(name)))
        out[name] = path.read_bytes()
    return out


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def image(folder: Path, n: int, commons_title: str, meta: dict) -> Path:
    """Download the painting at 1920 px wide (or its full size if smaller), after checking its license."""
    path = folder / "images" / f"day{n}.jpg"
    if str(n) not in meta or not path.exists():
        q = urllib.parse.urlencode({"action": "query", "titles": commons_title, "prop": "imageinfo",
                                    "iiprop": "url|size|extmetadata", "iiurlwidth": 1920, "format": "json"})
        page = next(iter(json.loads(fetch(f"https://commons.wikimedia.org/w/api.php?{q}"))["query"]["pages"].values()))
        info = page["imageinfo"][0]
        em = info.get("extmetadata", {})
        license_ = strip_html(em.get("LicenseShortName", {}).get("value"))
        if not license_.startswith(OK_LICENSES):
            raise SystemExit(f"{commons_title}: license is {license_!r}, not public domain or CC0")
        meta[str(n)] = {
            "title": commons_title,
            "thumb": info["thumburl"],
            "w": info["width"],
            "h": info["height"],
            "license": license_,
            "artist": strip_html(em.get("Artist", {}).get("value")),
            "date": strip_html(em.get("DateTimeOriginal", {}).get("value")),
            "page": info["descriptionurl"],
        }
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(fetch(info["thumburl"]))
    return path


def verses(folder: Path, ref: str) -> list[dict]:
    path = folder / "text" / (ref.replace(" ", "_").replace(":", "_") + ".json")
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        data = json.loads(fetch(f"https://bible-api.com/{urllib.parse.quote(ref)}?translation=web"))
        path.write_text(json.dumps([{"verse": v["verse"], "text": v["text"]} for v in data["verses"]], indent=1, ensure_ascii=False))
    return json.loads(path.read_text())


def plain(vs: list[dict]) -> str:
    """The passage as plain text, one poetic line per line."""
    lines = []
    for v in vs:
        lines += [ln.strip() for ln in v["text"].strip().split("\n") if ln.strip()]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------------

W, H = pymupdf.paper_rect("letter").br  # 612 x 792
M = 60  # side margin
TOP, BOTTOM = 54, 64  # bottom leaves room for the running footer
CONTENT_W = W - 2 * M
GAP = 28  # between passage columns


def css(pal: dict) -> str:
    faces = "".join(
        f"@font-face {{font-family: ebg; src: url({name}.ttf); font-weight: {w}; font-style: {s};}}"
        for name, w, s in [("EBGaramond-Regular", "normal", "normal"), ("EBGaramond-Italic", "normal", "italic"),
                           ("EBGaramond-SemiBold", "bold", "normal"), ("EBGaramond-SemiBoldItalic", "bold", "italic")]
    )
    a, ink = pal["accent"], pal["ink"]
    return faces + f"""
* {{ font-family: ebg; }}
body {{ color: {ink}; font-size: 12pt; margin: 0; }}
p {{ margin: 0 0 7pt 0; line-height: 1.4; }}
.kicker {{ font-size: 9pt; font-weight: bold; color: {a}; letter-spacing: 2pt; margin: 0 0 4pt 0; }}
h1 {{ font-size: 28pt; font-weight: normal; margin: 0 0 2pt 0; line-height: 1.1; }}
h2 {{ font-size: 25pt; font-weight: normal; margin: 0 0 1pt 0; line-height: 1.1; }}
.ref {{ font-style: italic; font-size: 13pt; color: #6b6259; margin: 0; }}
.label {{ font-size: 8.5pt; font-weight: bold; color: {a}; letter-spacing: 1.5pt; margin: 0 0 1pt 0; }}
.grace {{ font-style: italic; font-size: 12.5pt; line-height: 1.35; margin: 0 0 7pt 0; }}
.focus {{ font-size: 12pt; line-height: 1.35; margin: 0; }}
.caption {{ font-size: 8.5pt; color: #7b7268; text-align: center; margin: 0; line-height: 1.3; }}
.line {{ font-size: 11.5pt; line-height: 1.3; margin: 0 0 0 12pt; text-indent: -12pt; }}
.end {{ margin-bottom: 4pt; }}
.prose {{ font-size: 12pt; line-height: 1.45; margin: 0 0 6pt 0; }}
.vn {{ font-size: 7.5pt; color: {a}; font-weight: bold; }}
.whole {{ font-size: 11pt; line-height: 1.34; margin: 0 0 3pt 0; }}
.step {{ margin: 0 0 7pt 0; line-height: 1.38; }}
.stepname {{ font-weight: bold; color: {a}; }}
.intro {{ font-size: 12.5pt; line-height: 1.45; margin: 0 0 9pt 0; }}
.orn {{ text-align: center; color: {a}; font-size: 15pt; margin: 8pt 0 8pt 0; }}
.credit {{ font-size: 10.5pt; line-height: 1.35; margin: 0 0 7pt 0; }}
.small {{ font-size: 9.5pt; color: #6b6259; line-height: 1.35; }}
"""


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def is_poetry(vs: list[dict]) -> bool:
    return any("\n" in v["text"].strip() for v in vs)


def passage_html(vs: list[dict], cls: str = "verse") -> str:
    """Poetry keeps its line breaks, one paragraph per verse; prose runs on."""
    if is_poetry(vs) and cls == "verse":
        out = []
        for v in vs:
            lines = [esc(ln.strip()) for ln in v["text"].strip().split("\n") if ln.strip()]
            lines[0] = f'<span class="vn">{v["verse"]}</span>&#160;' + lines[0]
            last = len(lines) - 1
            # One paragraph per poetic line, so a line that wraps gets a hanging indent.
            out += [f'<p class="{"line end" if i == last else "line"}">{ln}</p>' for i, ln in enumerate(lines)]
        return "".join(out)
    out = []
    for v in vs:
        text = " ".join(ln.strip() for ln in v["text"].strip().split("\n") if ln.strip())
        out.append(f'<span class="vn">{v["verse"]}</span>&#160;{esc(text)} ')
    return f'<p class="{"prose" if cls == "verse" else cls}">' + "".join(out) + "</p>"


class Page:
    """Measure and draw a few Story blocks at chosen positions on one page."""

    def __init__(self, archive, style):
        self.archive, self.style = archive, style

    def story(self, html):
        return pymupdf.Story(html=html, user_css=self.style, archive=self.archive)

    def height(self, html, width=CONTENT_W, limit=2000):
        more, filled = self.story(html).place(pymupdf.Rect(0, 0, width, limit))
        return pymupdf.Rect(filled).y1 if not more else 1e9

    def split_columns(self, vs, gap=GAP):
        """Split a psalm between two columns at the verse break that balances them best."""
        cw = (CONTENT_W - gap) / 2
        best = None
        for k in range(1, len(vs)):
            left, right = passage_html(vs[:k]), passage_html(vs[k:])
            h = max(self.height(left, cw), self.height(right, cw))
            if best is None or h < best[2]:
                best = (left, right, h)
        return best

    def draw(self, dev, html, rect):
        s = self.story(html)
        more, filled = s.place(rect)
        s.draw(dev)
        return more, filled

    def draw_columns(self, dev, left, right, top, height, gap=GAP):
        cw = (CONTENT_W - gap) / 2
        more_l, _ = self.draw(dev, left, pymupdf.Rect(M, top, M + cw, top + height + 2))
        more_r, _ = self.draw(dev, right, pymupdf.Rect(M + cw + gap, top, W - M, top + height + 2))
        return more_l or more_r


def flow(writer, archive, style, html):
    """A flowing section (how to pray, credits) over as many pages as it needs. Returns page count."""
    story = pymupdf.Story(html=html, user_css=style, archive=archive)
    pages, more = 0, True
    while more:
        dev = writer.begin_page(pymupdf.Rect(0, 0, W, H))
        more, _ = story.place(pymupdf.Rect(M, TOP + 10, W - M, H - BOTTOM))
        story.draw(dev)
        writer.end_page()
        pages += 1
    return pages


def build(ex: dict, font_bytes: dict) -> tuple[Path, list[dict]]:
    folder = HERE / ex["slug"]
    (folder / "images").mkdir(parents=True, exist_ok=True)
    meta_path = folder / "images" / "images.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    pal = ex["palette"]
    style = css(pal)
    archive = pymupdf.Archive()
    for name, data in font_bytes.items():
        archive.add(data, f"{name}.ttf")
    pg = Page(archive, style)
    ndays = len(ex["days"])

    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    placements = []  # (page index, rect, image path) to insert after the text pages exist
    footers = {}  # page index -> footer text
    page_no = 0

    # --- Cover: typographic, on a dark ground (painted in afterwards) ----------------
    toc = "".join(
        f'<p style="margin: 0 0 5pt 0; font-size: 11.5pt; color: #e9e1d2;">'
        f'<span style="color: #cfae72; font-size: 9pt; font-weight: bold; letter-spacing: 1.5pt;">DAY {NUMBERS[i].upper()}</span>'
        f'&#160;&#160;{esc(d["title"])}&#160;&#160;<i style="color: #b9ad9c;">{esc(d["ref"])}</i></p>'
        for i, d in enumerate(ex["days"])
    )
    cover_top = (
        f'<div style="text-align: center;">'
        f'<p style="font-size: 10pt; font-weight: bold; letter-spacing: 3pt; color: #cfae72; margin: 0 0 22pt 0;">{esc(ex["kicker"].upper())}</p>'
        f'<p style="font-size: 72pt; color: #f4efe6; margin: 0; line-height: 1;">{esc(ex["title"])}</p>'
        f'<p style="font-size: 21pt; font-style: italic; color: #e3d8c5; margin: 10pt 0 0 0;">{esc(ex["subtitle"])}</p>'
        f'<p style="font-size: 20pt; color: #cfae72; margin: 26pt 0 26pt 0;">&#x2766;</p></div>'
    )
    cover_bottom = (
        '<div style="text-align: center;">'
        '<p style="font-size: 9.5pt; color: #b9ad9c; margin: 0 0 3pt 0;">Scripture from the World English Bible · Paintings from the public domain</p>'
        '<p style="font-size: 9pt; font-weight: bold; letter-spacing: 2.5pt; color: #cfae72; margin: 0;">IGNATIUS AT HOME</p></div>'
    )
    dev = writer.begin_page(pymupdf.Rect(0, 0, W, H))
    h_top = pg.height(cover_top, W - 2 * 90)
    h_toc = pg.height(f"<div>{toc}</div>", 330)
    y0 = (H - (h_top + h_toc)) / 2 - 30
    pg.draw(dev, cover_top, pymupdf.Rect(90, y0, W - 90, y0 + h_top + 5))
    toc_x = (W - 330) / 2 + 12
    pg.draw(dev, f"<div>{toc}</div>", pymupdf.Rect(toc_x, y0 + h_top, toc_x + 330, y0 + h_top + h_toc + 5))
    pg.draw(dev, cover_bottom, pymupdf.Rect(90, H - 112, W - 90, H - 60))
    writer.end_page()
    page_no += 1

    # --- How to pray -----------------------------------------------------------------
    steps = "".join(
        f'<p class="step"><span class="stepname">{i}. {esc(name)}.</span> {esc(text)}</p>'
        for i, (name, text) in enumerate(ex["steps"], start=1)
    )
    days_list = "".join(
        f'<p style="margin: 0 0 3pt 0;"><span class="kicker">DAY {NUMBERS[i].upper()}</span>&#160;&#160;{esc(d["title"])}'
        f' <i style="color: #6b6259;">· {esc(d["ref"])}</i></p>'
        for i, d in enumerate(ex["days"])
    )
    how = (
        f'<p class="kicker">BEFORE YOU BEGIN</p><h1>How to Pray These Days</h1>'
        f'<p class="orn">&#x2766;</p>'
        + "".join(f'<p class="intro">{esc(t)}</p>' for t in ex["intro"])
        + f'<p class="label" style="margin-top: 8pt;">EACH DAY</p>{steps}'
        + f'<p class="intro" style="font-style: italic; margin-top: 6pt;">{esc(ex["closing"])}</p>'
        + f'<p class="label" style="margin-top: 10pt;">THE DAYS</p>{days_list}'
    )
    n = flow(writer, archive, style, how)
    for i in range(n):
        footers[page_no + i] = "How to pray"
    page_no += n

    # --- One page per day ------------------------------------------------------------
    credits = []
    for i, d in enumerate(ex["days"], start=1):
        img = image(folder, i, d["file"], meta)
        pix = pymupdf.Pixmap(str(img))
        ratio = pix.width / pix.height
        vs = verses(folder, d["ref"])
        head = (f'<p class="kicker">DAY {NUMBERS[i - 1].upper()}</p><h2>{esc(d["title"])}</h2>'
                f'<p class="ref">{esc(d["ref"])}</p>')
        caption = f'<p class="caption"><i>{esc(d["work"])}</i>, {esc(d["artist"].split(" (")[0])}, {esc(d["date"])}</p>'
        prayer = (f'<p class="label">THE GRACE</p><p class="grace">{esc(d["grace"])}</p>'
                  f'<p class="label">FOCUS</p><p class="focus">{esc(d["focus"])}</p>')
        body = passage_html(vs)
        top, bottom = TOP, H - BOTTOM
        gap_img, gap = 14, 16
        h_head = pg.height(head)
        h_cap = pg.height(caption)
        h_prayer = pg.height(prayer)
        two_col = is_poetry(vs) and sum(len(v["text"].strip().split("\n")) for v in vs) > 14
        if two_col:
            left, right, h_body = pg.split_columns(vs)
        else:
            h_body = pg.height(body)
        rule = 12  # hairline between prayer and passage
        avail = bottom - top - h_head - h_cap - h_prayer - h_body - 2 * gap_img - 2 * gap - rule
        img_h = min(avail, CONTENT_W / ratio, 400)
        if img_h < 150:
            raise SystemExit(f"{ex['slug']} day {i}: only {avail:.0f}pt left for the painting")
        img_w = img_h * ratio

        dev = writer.begin_page(pymupdf.Rect(0, 0, W, H))
        y = top
        pg.draw(dev, head, pymupdf.Rect(M, y, W - M, y + h_head + 2)); y += h_head + gap_img
        placements.append((page_no, pymupdf.Rect((W - img_w) / 2, y, (W + img_w) / 2, y + img_h), img)); y += img_h + 5
        pg.draw(dev, caption, pymupdf.Rect(M, y, W - M, y + h_cap + 2)); y += h_cap + gap
        pg.draw(dev, prayer, pymupdf.Rect(M, y, W - M, y + h_prayer + 2)); y += h_prayer + gap
        rule_y = y; y += rule
        if two_col:
            more = pg.draw_columns(dev, left, right, y, h_body)
        else:
            more, _ = pg.draw(dev, body, pymupdf.Rect(M, y, W - M, y + h_body + 2))
        if more:
            raise SystemExit(f"{ex['slug']} day {i}: passage overflowed")
        writer.end_page()
        footers[page_no] = f"Day {NUMBERS[i - 1]} · {d['title']}"
        placements.append((page_no, ("rule", rule_y), None))
        page_no += 1

        # The last Beatitudes day returns to the whole passage on a facing page.
        if d.get("whole"):
            whole = verses(folder, d["whole"])
            html = (f'<p class="kicker">DAY {NUMBERS[i - 1].upper()}, CONTINUED</p><h2>Return to the Whole</h2>'
                    f'<p class="ref">{esc(d["whole"])}</p><p class="orn">&#x2766;</p>'
                    f'<p class="intro" style="font-style: italic;">Now read the Beatitudes from the beginning, slowly, as if you were hearing them for the first time. '
                    f'Pause after each one. Notice which one Jesus seems to be saying to you.</p>'
                    + passage_html(whole))
            n = flow(writer, archive, style, html)
            for k in range(n):
                footers[page_no + k] = f"Day {NUMBERS[i - 1]} · {d['whole']}"
            page_no += n

        credits.append({"day": i, "artist": d["artist"], "title": d["work"], "date": d["date"],
                        "collection": d["collection"], "license": meta[str(i)]["license"],
                        "source": meta[str(i)]["page"]})

    meta_path.write_text(json.dumps(meta, indent=1, ensure_ascii=False))

    # --- Credits ---------------------------------------------------------------------
    rows = "".join(
        f'<p class="credit"><span class="kicker">DAY {NUMBERS[c["day"] - 1].upper()}</span><br/>'
        f'{esc(c["artist"])}, <i>{esc(c["title"])}</i>, {esc(c["date"])}. {esc(c["collection"])}. '
        f'<span class="small">{esc(c["license"])}, via Wikimedia Commons.</span></p>'
        for c in credits
    )
    html = (
        '<p class="kicker">ACKNOWLEDGMENTS</p><h1>Credits</h1><p class="orn">&#x2766;</p>'
        '<p class="label">SCRIPTURE</p>'
        '<p class="credit">Scripture: World English Bible, public domain. Text from bible-api.com.</p>'
        f'<p class="label" style="margin-top: 8pt;">PAINTINGS</p>{rows}'
        '<p class="label" style="margin-top: 8pt;">TYPE</p>'
        '<p class="credit">Set in EB Garamond by Georg Duffner and Octavio Pardo, SIL Open Font License.</p>'
        '<p class="small" style="margin-top: 14pt;">This booklet is a sample source document for Ignatius at Home, '
        'which turns scripture passages and images into a guided audio prayer retreat. '
        'It may be freely copied and shared.</p>'
    )
    n = flow(writer, archive, style, html)
    for k in range(n):
        footers[page_no + k] = "Credits"
    page_no += n
    writer.close()

    # --- Paint grounds, paintings, rules and footers ------------------------------------
    doc = pymupdf.open(stream=buf.getvalue(), filetype="pdf")
    gold = pal["gold"]
    ivory = (0.988, 0.976, 0.953)
    accent = tuple(int(pal["accent"][k:k + 2], 16) / 255 for k in (1, 3, 5))
    for page in doc:
        if page.number == 0:
            page.draw_rect(page.rect, color=None, fill=pal["cover"], overlay=False)
            page.draw_rect(page.rect + (28, 28, -28, -28), color=gold, width=0.9)
            page.draw_rect(page.rect + (34, 34, -34, -34), color=gold, width=0.4)
        else:
            page.draw_rect(page.rect, color=None, fill=ivory, overlay=False)
    font = pymupdf.Font(fontbuffer=font_bytes["EBGaramond-Italic"])
    for idx, text in footers.items():
        page = doc[idx]
        page.draw_line((M, H - 44), (W - M, H - 44), color=accent, width=0.4)
        tw = pymupdf.TextWriter(page.rect)
        tw.append((M, H - 30), f"{ex['title']}: {ex['subtitle']}", font=font, fontsize=8.5)
        right = f"{text}  ·  {idx + 1}"
        tw.append((W - M - font.text_length(right, 8.5), H - 30), right, font=font, fontsize=8.5)
        tw.write_text(page, color=(0.45, 0.42, 0.38))
    for idx, rect, img in placements:
        page = doc[idx]
        if img is None:  # a short ornamental rule above the passage
            _, y = rect
            page.draw_line((W / 2 - 40, y + 4), (W / 2 + 40, y + 4), color=accent, width=0.5)
            continue
        shadow = rect + (2.5, 2.5, 2.5, 2.5)
        page.draw_rect(shadow, color=None, fill=(0.86, 0.83, 0.78), overlay=True)
        page.insert_image(rect, filename=str(img))
        page.draw_rect(rect, color=(0.35, 0.3, 0.25), width=0.4)
    doc.subset_fonts()
    doc.set_metadata({"title": f"{ex['title']}: {ex['subtitle']}", "author": "Ignatius at Home",
                      "subject": ex["description"], "creator": "make_examples.py"})
    out = HERE / f"{ex['slug']}.pdf"
    doc.save(out, garbage=4, deflate=True)

    # A ~600 px cover image for the New retreat page, from the first day's painting.
    first = pymupdf.Pixmap(str(folder / "images" / "day1.jpg"))
    small = pymupdf.Pixmap(first, 600, round(600 * first.height / first.width))
    (folder / "cover.jpg").write_bytes(small.tobytes("jpeg", jpg_quality=85))
    return out, credits


def write_text_version(ex: dict) -> Path:
    """The same retreat as plain text (no images), to show a .txt upload."""
    folder = HERE / ex["slug"]
    parts = [f"{ex['title'].upper()}: {ex['subtitle']}", ex["kicker"], "",
             "HOW TO PRAY THESE DAYS", ""]
    parts += [t + "\n" for t in ex["intro"]]
    parts += ["Each day:"]
    parts += [f"{i}. {name}. {text}" for i, (name, text) in enumerate(ex["steps"], start=1)]
    parts += ["", ex["closing"], ""]
    for i, d in enumerate(ex["days"], start=1):
        parts += ["", f"DAY {i}: {d['title']}", d["ref"], "",
                  f"Grace: {d['grace']}", f"Focus: {d['focus']}", "",
                  plain(verses(folder, d["ref"])), ""]
    parts += ["", "Scripture: World English Bible, public domain."]
    out = HERE / f"{ex['slug']}.txt"
    out.write_text("\n".join(parts).replace("\n\n\n", "\n\n").strip() + "\n")
    return out


def main() -> None:
    font_bytes = fonts()
    listing = []
    for ex in EXAMPLES:
        out, credits = build(ex, font_bytes)
        doc = pymupdf.open(out)
        print(out.name, f"{out.stat().st_size / 1e6:.1f} MB", doc.page_count, "pages")
        listing.append({
            "slug": ex["slug"],
            "title": ex["title"],
            "subtitle": ex["subtitle"],
            "description": ex["description"],
            "days": len(ex["days"]),
            "file": out.name,
            "cover_image": f"{ex['slug']}/cover.jpg",
            "credits": {
                "scripture": "World English Bible, public domain",
                "images": [f"Day {c['day']}: {c['artist']}, {c['title']}, {c['date']}. {c['collection']}. "
                           f"{c['license']}, via Wikimedia Commons." for c in credits],
                "type": "EB Garamond, SIL Open Font License",
            },
        })
    (HERE / "examples.json").write_text(json.dumps(listing, indent=2, ensure_ascii=False) + "\n")
    print(write_text_version(EXAMPLES[0]).name)


if __name__ == "__main__":
    main()
