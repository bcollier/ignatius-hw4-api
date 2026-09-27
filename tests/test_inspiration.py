"""A retreat from an idea (and a photo): passages chosen, text copied from the photo or
fetched from the World English Bible, then planned like any document."""

import asyncio
import time

from fastapi.testclient import TestClient

from app import inspiration, main
from app.extract import Extracted, Image


def wait_for(client, url, done):
    for _ in range(100):
        body = client.get(url).json()
        if done(body):
            return body
        time.sleep(0.05)
    raise AssertionError(f"timed out: {body}")

OUTLINE = {"title": "Stories He Told", "idea": "The parables of Jesus.", "days": [
    {"title": "The Sower", "reference": "Matthew 13:1-9", "from_photo": "", "why": "w"},
    {"title": "The Card", "reference": "Psalm 23:1", "from_photo": "The Lord is my shepherd.", "why": "w"},
]}


def test_the_source_uses_the_photo_words_and_the_bible(monkeypatch):
    async def outline(*args):
        return {**OUTLINE, "days": [dict(d) for d in OUTLINE["days"]]}

    async def lookup(day):
        if day["from_photo"]:
            return await real(day)
        return "Matthew 13:1-9 (World English Bible)", "On that day Jesus went out of the house."

    real = inspiration._passage
    monkeypatch.setattr(inspiration, "_outline", outline)
    monkeypatch.setattr(inspiration, "_passage", lookup)
    photo = Image(b"jpeg", "image/jpeg", 10, 10, 1)
    filename, source = asyncio.run(inspiration.compose("the parables", photo, 2, "anthropic/claude-opus-5.5"))
    assert filename == "Stories He Told.txt"
    assert "Day 1: The Sower\nMatthew 13:1-9 (World English Bible)\n\nOn that day" in source.text
    assert "Psalm 23:1 (as printed in the photo)\n\nThe Lord is my shepherd." in source.text
    assert source.images == [photo]


def test_references_are_cleaned_for_the_lookup():
    assert inspiration._plain_ref("Luke 15:11–32 (NRSV)") == "Luke 15:11-32"


def test_an_idea_becomes_a_retreat(monkeypatch):
    async def compose(idea, photo, days, model):
        assert idea == "the parables" and days == 3 and photo is None
        return "Stories He Told.txt", Extracted(kind="idea", text="Day 1: The Sower\nMatthew 13:1-9\n\nText.", page_count=0)

    monkeypatch.setattr(inspiration, "compose", compose)
    with TestClient(main.app) as client:
        assert client.post("/api/retreats", data={"idea": "x", "idea_days": "0"}).status_code == 400
        r = client.post("/api/retreats", data={"idea": "the parables", "idea_days": "3"})
        assert r.status_code == 202, r.text
        retreat = wait_for(client, f"/api/retreats/{r.json()['id']}", lambda b: b["status"] != "planning")
        assert retreat["status"] == "ready", retreat.get("error")
        assert retreat["filename"] == "Stories He Told.txt"
