import time
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import main, pipeline, tts
from app.storage import store

SAMPLES = Path(__file__).parent.parent / "samples"


@pytest.fixture
def client(monkeypatch):
    async def fake_synthesize(text, voice, out_path):
        tts.tier_of(voice)
        out_path.write_bytes(b"ID3fake-mp3")
        return 12.5

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    with TestClient(main.app) as c:
        yield c


def wait_for(client, url, done):
    for _ in range(100):
        body = client.get(url).json()
        if done(body):
            return body
        time.sleep(0.05)
    raise AssertionError(f"timed out: {body}")


def upload(client, name):
    with open(SAMPLES / name, "rb") as f:
        return client.post("/api/retreats", files={"file": (name, f)})


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] and body["llm"] == "stub" and "free" in body["tiers"] and body["sign_in"] is False


def test_rejects_non_document(client):
    r = client.post("/api/retreats", files={"file": ("notes.txt", b"hello")})
    assert r.status_code == 400
    assert "PDF" in r.json()["error"]["message"]


def test_rejects_pdf_without_text_or_images(client):
    doc = pymupdf.open()
    doc.new_page()
    r = client.post("/api/retreats", files={"file": ("blank.pdf", doc.tobytes())})
    assert r.status_code == 400
    assert r.json()["error"]["message"] == "No text found in this document."


def test_unknown_retreat_is_404(client):
    r = client.get("/api/retreats/nope")
    assert r.status_code == 404 and "not found" in r.json()["error"]["message"]


def test_pdf_to_plan_to_audio(client):
    r = upload(client, "loose-passages-web.pdf")
    assert r.status_code == 202
    retreat = r.json()
    assert retreat["status"] == "planning" and retreat["source"]["images"] == 1

    url = f"/api/retreats/{retreat['id']}"
    retreat = wait_for(client, url, lambda b: b["status"] != "planning")
    assert retreat["status"] == "ready"
    assert len(retreat["plan"]["days"]) == 7
    assert client.get(retreat["images"][0]["url"]).headers["content-type"] == "image/jpeg"
    assert retreat["id"] in [r["id"] for r in client.get("/api/retreats").json()["retreats"]]

    voices = {"guide": "en-US-AvaMultilingualNeural", "reading": "en-US-AndrewMultilingualNeural",
              "heart": "en-US-EmmaMultilingualNeural", "deep": "en-US-ChristopherNeural"}
    r = client.post(f"{url}/days/1/build", json={"voices": voices, "guide": {"closing": ""}})
    assert r.status_code == 202
    retreat = wait_for(client, url, lambda b: b["days"]["1"]["status"] != "building")
    day = retreat["days"]["1"]
    assert day["status"] == "ready", day
    for track in ("reading", "heart", "deep"):
        assert day["tracks"][track]["status"] == "ready" and day["tracks"][track]["seconds"] == 12.5
        assert day["tracks"][track]["voice"] == voices[track]
        audio = client.get(day["tracks"][track]["url"])
        assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg"
    # Spoken guidance: every default clip except the one left empty, in the guide voice.
    assert set(day["guide"]) == {"opening", "first", "second", "third", "silence", "last"}
    assert all(c["status"] == "ready" and c["voice"] == voices["guide"] and c["url"] for c in day["guide"].values())
    assert day["guide"]["opening"]["script"].startswith("Day 1.")

    # Re-recording with a new voice keeps the written scripts.
    heart_script = day["tracks"]["heart"]["script"]
    r = client.post(f"{url}/days/1/build", json={"voice": "en-US-AriaNeural", "keep_scripts": True})
    assert r.status_code == 202
    day = wait_for(client, url, lambda b: b["days"]["1"]["status"] != "building")["days"]["1"]
    assert day["tracks"]["heart"]["script"] == heart_script and day["tracks"]["heart"]["voice"] == "en-US-AriaNeural"


def test_docx_with_days_keeps_them(client):
    retreat = upload(client, "three-days-called-by-name-web.docx").json()
    retreat = wait_for(client, f"/api/retreats/{retreat['id']}", lambda b: b["status"] != "planning")
    assert retreat["plan"]["mode"] == "follows_source"
    assert [d["title"] for d in retreat["plan"]["days"]][:2] == ["Day 1: Isaiah 43:1-4", "Day 2: Luke 1:26-38"]


def test_build_rejects_unknown_voice_and_day(client):
    retreat = upload(client, "loose-passages-web.pdf").json()
    url = f"/api/retreats/{retreat['id']}"
    wait_for(client, url, lambda b: b["status"] != "planning")
    r = client.post(f"{url}/days/1/build", json={"voice": "nobody"})
    assert r.status_code == 400
    r = client.post(f"{url}/days/99/build", json={})
    assert r.status_code == 404


def test_retreat_survives_restart_and_is_private(client):
    retreat = upload(client, "loose-passages-web.pdf").json()
    url = f"/api/retreats/{retreat['id']}"
    wait_for(client, url, lambda b: b["status"] != "planning")
    pipeline.active.clear()  # as if the server restarted: only storage remains
    assert client.get(url).json()["status"] == "ready"

    stored = (store.rows / f"{retreat['id']}.json")
    stored.write_text(stored.read_text().replace("00000000-0000-0000-0000-000000000000", "someone-else"))
    assert client.get(url).status_code == 404


def test_interrupted_build_is_marked_failed(client):
    retreat = upload(client, "loose-passages-web.pdf").json()
    url = f"/api/retreats/{retreat['id']}"
    retreat = wait_for(client, url, lambda b: b["status"] != "planning")
    stored = store.rows / f"{retreat['id']}.json"
    stored.write_text(stored.read_text().replace('"status": "idle"', '"status": "building"', 1))
    pipeline.active.clear()
    day = client.get(url).json()["days"]["1"]
    assert day["status"] == "failed" and "restart" in day["error"]


def test_delete_removes_retreat(client):
    retreat = upload(client, "loose-passages-web.pdf").json()
    url = f"/api/retreats/{retreat['id']}"
    wait_for(client, url, lambda b: b["status"] != "planning")
    assert client.delete(url).status_code == 200
    assert client.get(url).status_code == 404
