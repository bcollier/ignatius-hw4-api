import json
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


def test_options_list_models_with_prices(client):
    body = client.get("/api/options").json()
    ids = [m["id"] for m in body["models"]]
    assert body["default_model"] in ids and "anthropic/claude-haiku-4.5" in ids
    assert all(m["input_per_m"] > 0 and m["output_per_m"] > 0 for m in body["models"])


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] and body["llm"] == "stub" and "free" in body["tiers"] and body["sign_in"] is False


def test_rejects_non_document(client):
    r = client.post("/api/retreats", files={"file": ("notes.rtf", b"hello")})
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
    r = client.post(f"{url}/days/1/build", json={"voices": voices, "guide": {"closing": ""}, "model": "anthropic/claude-sonnet-5"})
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
    # Free voices cost nothing; every character is counted.
    assert day["cost"]["voice_usd"] == 0 and day["cost"]["voice_characters"]["free"] > 0
    assert day["cost"]["llm"]["model"] == "anthropic/claude-sonnet-5"

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
    r = client.post(f"{url}/days/1/build", json={"model": "gpt-9"})
    assert r.status_code == 400 and "Unknown model" in r.json()["error"]["message"]


def test_retreat_survives_restart_and_is_private(client):
    retreat = upload(client, "loose-passages-web.pdf").json()
    url = f"/api/retreats/{retreat['id']}"
    wait_for(client, url, lambda b: b["status"] != "planning")
    pipeline.active.clear()  # as if the server restarted: only storage remains
    assert client.get(url).json()["status"] == "ready"

    stored = (store.rows / f"{retreat['id']}.json")
    stored.write_text(stored.read_text().replace("00000000-0000-0000-0000-000000000000", "someone-else"))
    assert client.get(url).status_code == 404


def test_interrupted_planning_resumes(client):
    retreat = upload(client, "loose-passages-web.pdf").json()
    url = f"/api/retreats/{retreat['id']}"
    wait_for(client, url, lambda b: b["status"] != "planning")
    # As if the server died mid-plan: stored as planning, old heartbeat, no running task.
    stored = store.rows / f"{retreat['id']}.json"
    data = json.loads(stored.read_text())
    data.update(status="planning", plan=None, days={}, heartbeat=0)
    stored.write_text(json.dumps(data))
    pipeline.active.clear()
    body = wait_for(client, url, lambda b: b["status"] != "planning")
    assert body["status"] == "ready" and body["resumes"] == 1 and len(body["plan"]["days"]) == 7


def test_recent_heartbeat_is_left_alone(client):
    """During a zero-downtime deploy the old server may still be working."""
    retreat = upload(client, "loose-passages-web.pdf").json()
    url = f"/api/retreats/{retreat['id']}"
    wait_for(client, url, lambda b: b["status"] != "planning")
    stored = store.rows / f"{retreat['id']}.json"
    data = json.loads(stored.read_text())
    data.update(status="planning", heartbeat=time.time())
    stored.write_text(json.dumps(data))
    pipeline.active.clear()
    body = client.get(url).json()
    assert body["status"] == "planning" and "resumes" not in body


def test_interrupted_build_resumes_and_keeps_finished_work(client, monkeypatch):
    retreat = upload(client, "loose-passages-web.pdf").json()
    url = f"/api/retreats/{retreat['id']}"
    wait_for(client, url, lambda b: b["status"] != "planning")
    client.post(f"{url}/days/1/build", json={"voice": "en-US-AvaMultilingualNeural"})
    wait_for(client, url, lambda b: b["days"]["1"]["status"] != "building")
    # Pretend the server died after the reading and the heart reflection were done.
    stored = store.rows / f"{retreat['id']}.json"
    data = json.loads(stored.read_text())
    day = data["days"]["1"]
    day["status"] = "building"
    day["tracks"]["deep"] = {"status": "writing"}
    day["guide"]["closing"] = {"status": "waiting"}
    data["heartbeat"] = 0
    stored.write_text(json.dumps(data))
    pipeline.active.clear()
    recorded = []
    real = tts.synthesize

    async def spy(text, voice, out_path):
        recorded.append(text[:20])
        return await real(text, voice, out_path)

    monkeypatch.setattr(tts, "synthesize", spy)
    body = wait_for(client, url, lambda b: b["days"]["1"]["status"] != "building")
    assert body["days"]["1"]["status"] == "ready" and body["resumes"] == 1
    assert len(recorded) == 2  # only the deep dive and the closing were redone


def test_gives_up_after_too_many_resumes(client):
    retreat = upload(client, "loose-passages-web.pdf").json()
    url = f"/api/retreats/{retreat['id']}"
    wait_for(client, url, lambda b: b["status"] != "planning")
    stored = store.rows / f"{retreat['id']}.json"
    data = json.loads(stored.read_text())
    data.update(status="planning", heartbeat=0, resumes=pipeline.MAX_RESUMES)
    stored.write_text(json.dumps(data))
    pipeline.active.clear()
    body = client.get(url).json()
    assert body["status"] == "failed" and "too many times" in body["error"]


def test_delete_removes_retreat(client):
    retreat = upload(client, "loose-passages-web.pdf").json()
    url = f"/api/retreats/{retreat['id']}"
    wait_for(client, url, lambda b: b["status"] != "planning")
    assert client.delete(url).status_code == 200
    assert client.get(url).status_code == 404


def test_script_pdf_follows_the_prayer(client):
    retreat = upload(client, "loose-passages-web.pdf").json()
    url = f"/api/retreats/{retreat['id']}"
    wait_for(client, url, lambda b: b["status"] != "planning")
    client.post(f"{url}/days/1/build", json={"voice": "en-US-AvaMultilingualNeural"})
    wait_for(client, url, lambda b: b["days"]["1"]["status"] != "building")

    r = client.get(f"{url}/script.pdf", params={"day": 1, "pause": 60, "grace_silence": 20})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    text = "".join(page.get_text() for page in pymupdf.open(stream=r.content, filetype="pdf"))
    order = ["Opening", "Silence, 20 seconds", "First reading", "For the heart", "Second reading",
             "Deep dive", "Third reading", "silence, 1 minute", "Last reading", "Closing"]
    positions = [text.find(label) for label in order]
    assert all(p >= 0 for p in positions) and positions == sorted(positions), list(zip(order, positions, strict=False))

    whole = client.get(f"{url}/script.pdf")
    doc = pymupdf.open(stream=whole.content, filetype="pdf")
    assert doc.page_count >= 8  # cover + one page or more per day
    assert "haven't been written yet" in "".join(p.get_text() for p in doc)  # unbuilt days still appear
    assert client.get(f"{url}/script.pdf", params={"day": 99}).status_code == 404
