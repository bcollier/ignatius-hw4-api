"""Making a whole retreat from one upload, and the prayer-tracking endpoints."""

import asyncio
import json
import time
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import config, jetstream, llm, main, pipeline, pricing, prompts, tts
from app.auth import User, current_user
from app.storage import store

SAMPLE = Path(__file__).parent.parent / "samples" / "loose-passages-web.pdf"
DOCX = Path(__file__).parent.parent / "samples" / "three-days-called-by-name-web.docx"
VOICES = {"guide": "en-US-AvaMultilingualNeural", "reading": "en-US-AndrewMultilingualNeural",
          "heart": "en-US-AndrewMultilingualNeural", "deep": "en-US-ChristopherNeural"}


@pytest.fixture
def client(monkeypatch):
    async def fake_synthesize(text, voice, out_path):
        out_path.write_bytes(b"ID3")
        return 2.0

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    with TestClient(main.app) as c:
        yield c


def make(client, path=DOCX, **form):
    form.setdefault("options", json.dumps({"voices": VOICES}))
    with open(path, "rb") as f:
        return client.post("/api/retreats", files={"file": (path.name, f)}, data=form)


def until(client, rid, done, tries=200):
    for _ in range(tries):
        body = client.get(f"/api/retreats/{rid}").json()
        if done(body):
            return body
        time.sleep(0.05)
    raise AssertionError(json.dumps(body)[:800])


def finished(body):
    return body["status"] in ("ready", "failed")


def test_one_upload_makes_every_day(client):
    r = make(client, start_date="2026-10-05")
    assert r.status_code == 202 and r.json()["status"] == "planning"
    body = until(client, r.json()["id"], finished)
    assert body["status"] == "ready" and body["start_date"] == "2026-10-05"
    assert all(d["status"] == "ready" for d in body["days"].values())
    assert body["progress"]["done"] == body["progress"]["total"] == 3 and body["progress"]["failed"] == []
    day = body["days"]["2"]
    assert set(day["tracks"]) == {"reading", "heart", "deep"} and day["guide"]["opening"]["url"]
    summary = next(r for r in client.get("/api/retreats").json()["retreats"] if r["id"] == body["id"])
    assert summary["days_built"] == 3 and [d["status"] for d in summary["day_states"]] == ["ready"] * 3


def test_without_options_only_the_plan_is_made(client):
    with open(DOCX, "rb") as f:
        rid = client.post("/api/retreats", files={"file": ("d.docx", f)}).json()["id"]
    body = until(client, rid, finished)
    assert body["status"] == "ready" and all(d["status"] == "idle" for d in body["days"].values())


def test_bad_options_fail_before_the_upload(client):
    r = make(client, options=json.dumps({"voices": {**VOICES, "heart": "nobody"}}))
    assert r.status_code == 400 and "voice" in r.json()["error"]["message"].lower()
    assert make(client, options="{not json").status_code == 400
    assert make(client, start_date="next tuesday").status_code == 400


def test_free_user_cannot_ask_for_premium_voices_at_upload(client, monkeypatch):
    monkeypatch.setattr(config, "JETSTREAM_API_KEY", "t")
    monkeypatch.setattr(config, "FREE_MODE", True)
    main.app.dependency_overrides[current_user] = lambda: User("guest-1", "", full=False, anonymous=True)
    try:
        r = make(client, options=json.dumps({"voices": {**VOICES, "heart": "nPczCjzI2devNBz1zQrb"}}))
        assert r.status_code == 403
    finally:
        main.app.dependency_overrides.clear()


def test_a_failed_day_does_not_stop_the_rest(client, monkeypatch):
    async def flaky(text, voice, out_path):
        if "Luke" in text or "Gabriel" in text:  # day 2's reading
            raise tts.TTSError("The free voice service didn't respond.")
        out_path.write_bytes(b"ID3")
        return 2.0

    monkeypatch.setattr(tts, "synthesize", flaky)
    body = until(client, make(client).json()["id"], finished)
    assert body["status"] == "ready"
    assert body["days"]["2"]["status"] == "failed" and body["progress"]["failed"] == [2]
    assert body["days"]["1"]["status"] == body["days"]["3"]["status"] == "ready"


def test_building_resumes_after_a_restart(client):
    body = until(client, make(client).json()["id"], finished)
    stored = store.rows / f"{body['id']}.json"
    data = json.loads(stored.read_text())
    data.update(status="building", heartbeat=0)
    data["days"]["3"] = {"status": "queued", "error": None, "tracks": {}, "guide": {}, "cost": None}
    stored.write_text(json.dumps(data))
    pipeline.active.clear()
    body = until(client, body["id"], lambda b: b["status"] == "ready" and b["days"]["3"]["status"] == "ready")
    assert body["resumes"] == 1 and body["progress"]["done"] == 3


def test_single_day_rebuild_waits_for_the_retreat(client):
    rid = make(client).json()["id"]
    until(client, rid, lambda b: b["status"] == "building")
    r = client.post(f"/api/retreats/{rid}/days/1/build", json={"voices": VOICES})
    assert r.status_code == 409
    until(client, rid, finished)


def test_prayed_and_journal(client):
    rid = until(client, make(client).json()["id"], finished)["id"]
    url = f"/api/retreats/{rid}/days/2/prayed"
    body = client.post(url, json={"word": "called by name", "note": "Felt known."}).json()
    day = body["days"]["2"]
    assert day["prayed_at"] and day["journal"]["word"] == "called by name"
    assert client.post(url, json={"word": "x" * 101}).status_code == 400
    body = client.post(url, json={"prayed": False, "word": "", "note": ""}).json()
    assert body["days"]["2"]["prayed_at"] is None and body["days"]["2"]["journal"] is None
    assert client.post(f"/api/retreats/{rid}/days/9/prayed", json={}).status_code == 404


def test_listening_progress_and_finishing(client):
    rid = until(client, make(client).json()["id"], finished)["id"]
    url = f"/api/retreats/{rid}/days/1/progress"
    client.post(url, json={"step": 3, "part": "First reading", "seconds": 12, "parts_played": ["opening", "first"]})
    r = client.post(url, json={"step": 6, "part": "For the heart", "seconds": 40, "parts_played": ["reading1", "first"]}).json()
    assert r["listening"]["parts_played"] == ["opening", "first", "reading1"] and r["prayed_at"] is None
    states = next(s for s in client.get("/api/retreats").json()["retreats"] if s["id"] == rid)["day_states"]
    assert states[0]["started"] and not states[0]["finished"]
    r = client.post(url, json={"step": 20, "part": "Closing", "finished": True}).json()
    assert r["prayed_at"] and r["listening"]["finished_at"]
    summary = next(s for s in client.get("/api/retreats").json()["retreats"] if s["id"] == rid)
    assert summary["days_prayed"] == 1 and summary["day_states"][0]["finished"]


def test_start_date_and_title_can_change(client):
    rid = until(client, make(client).json()["id"], finished)["id"]
    body = client.patch(f"/api/retreats/{rid}", json={"start_date": "2026-11-01", "title": "Called by Name"}).json()
    assert body["start_date"] == "2026-11-01" and body["plan"]["title"] == "Called by Name"
    assert client.patch(f"/api/retreats/{rid}", json={"start_date": "soon"}).status_code == 400


def test_pdf_has_journal_and_series(client):
    first = until(client, make(client).json()["id"], finished)["id"]
    second = until(client, make(client, series=first).json()["id"], finished)["id"]
    client.post(f"/api/retreats/{second}/days/1/prayed", json={"word": "fear not"})
    pdf = client.get(f"/api/retreats/{second}/script.pdf")
    text = "".join(p.get_text() for p in pymupdf.open(stream=pdf.content, filetype="pdf"))
    assert "Week 2 of a series" in text and "After praying" in text and "fear not" in text


def test_parts_know_about_each_other(client, monkeypatch):
    seen = {}

    async def heart(context, instructions, words, meter, series_text=""):
        return "HEART: notice the word 'mine'."

    async def deep(context, instructions, words, meter, provider=None, series_text=""):
        seen["deep_context"] = context
        return "DEEP: the Hebrew verb for 'called'.", [], False

    async def tailor(context, heart_script, deep_script, lines, meter):
        seen["tailor"] = (heart_script, deep_script, set(lines))
        return {n: t + " (tailored)" for n, t in lines.items()}

    monkeypatch.setattr(llm, "write_heart", heart)
    monkeypatch.setattr(llm, "write_deep", deep)
    monkeypatch.setattr(llm, "tailor_guide", tailor)
    body = until(client, make(client).json()["id"], finished)
    assert "<heart_reflection>" in seen["deep_context"] and "notice the word 'mine'" in seen["deep_context"]
    assert seen["tailor"][0].startswith("HEART") and seen["tailor"][1].startswith("DEEP")
    assert body["days"]["1"]["guide"]["second"]["script"].endswith("(tailored)")

    # Re-recording keeps the tailored guidance instead of tailoring again.
    seen.clear()
    client.post(f"/api/retreats/{body['id']}/days/1/build", json={"voices": VOICES, "keep_scripts": True})
    body = until(client, body["id"], lambda b: b["days"]["1"]["status"] == "ready" and b["days"]["1"]["guide"]["second"].get("url"))
    assert "tailor" not in seen and body["days"]["1"]["guide"]["second"]["script"].endswith("(tailored)")


def test_tailoring_keeps_defaults_for_bad_lines(monkeypatch):
    monkeypatch.setattr(config, "LLM_MODE", "openrouter")
    lines = {"opening": "Day 1. Settle. Ask for this grace: to be known. Stay with it.", "second": "The second reading.",
             "closing": "Amen."}

    async def fake(model, system, text, meter, images=(), max_tokens=16000):
        return json.dumps({"opening": "Day 1. Settle, and rest.",  # dropped the grace: rejected
                           "second": "Now again: listen for the word 'mine' the reflection held up.",
                           "closing": ""})  # empty: rejected

    monkeypatch.setattr(jetstream, "complete", fake)
    out = asyncio.run(llm.tailor_guide("ctx", "heart", "deep", lines, pricing.Meter("jetstream/muse-glimmer", {})))
    assert out["opening"] == lines["opening"] and out["closing"] == "Amen."
    assert "the word 'mine'" in out["second"]


def test_plan_images_per_day_are_cleaned():
    plan = {"days": [
        {"day": 1, "title": "a", "passage_text": "p", "image_index": 2, "image_indexes": [0, 9, 0]},
        {"day": 2, "title": "b", "passage_text": "p", "image_index": -1, "image_indexes": []},
        {"day": 3, "title": "c", "passage_text": "p", "image_index": 1},
    ]}
    days = llm._clean_plan(plan, image_count=3)["days"]
    assert days[0]["image_indexes"] == [2, 0] and days[0]["image_index"] == 2
    assert days[1]["image_indexes"] == [] and days[1]["image_index"] == -1
    assert days[2]["image_indexes"] == [1]


def test_try_again_records_only_what_failed(client, monkeypatch):
    calls = []
    fail = {"on": True}

    async def flaky(text, voice, out_path):
        calls.append(text[:30])
        if fail["on"] and "silence" not in text.lower() and len(calls) % 3 == 0:
            raise tts.TTSError("ElevenLabs is busy (too many requests at once). Try again in a minute.")
        out_path.write_bytes(b"ID3")
        return 2.0

    monkeypatch.setattr(tts, "synthesize", flaky)
    rid = make(client).json()["id"]
    body = until(client, rid, finished)
    failed = [n for n, d in body["days"].items() if d["status"] == "failed"]
    assert failed
    n = failed[0]
    ready_before = {k for g in ("tracks", "guide") for k, c in body["days"][n][g].items() if c["status"] == "ready"}
    written = sum(1 for _ in calls)
    fail["on"] = False
    calls.clear()
    assert client.post(f"/api/retreats/{rid}/days/{n}/retry").status_code == 202
    day = until(client, rid, lambda b: b["days"][n]["status"] in ("ready", "failed"))["days"][n]
    assert day["status"] == "ready" and 0 < len(calls) < written
    assert len(calls) == sum(1 for g in ("tracks", "guide") for k in day[g] if k not in ready_before)
    assert client.post(f"/api/retreats/{rid}/days/{n}/retry").status_code == 409  # nothing left to retry
    assert day["cost"]["llm"]["input_tokens"] == body["days"][n]["cost"]["llm"]["input_tokens"]  # writing cost kept
