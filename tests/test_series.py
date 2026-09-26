"""Retreats in a series pass their earlier weeks to the model."""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import llm, main, pipeline, series, tts
from app.storage import store

SAMPLE = Path(__file__).parent.parent / "samples" / "loose-passages-web.pdf"


@pytest.fixture
def client(monkeypatch):
    async def fake_synthesize(text, voice, out_path):
        out_path.write_bytes(b"ID3")
        return 1.0

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    with TestClient(main.app) as c:
        yield c


def upload(client, **form):
    with open(SAMPLE, "rb") as f:
        return client.post("/api/retreats", files={"file": ("week.pdf", f)}, data=form)


def ready(client, rid, done=lambda b: b["status"] != "planning"):
    for _ in range(100):
        body = client.get(f"/api/retreats/{rid}").json()
        if done(body):
            return body
        time.sleep(0.05)
    raise AssertionError(body)


def test_series_goes_to_planning_and_writing(client, monkeypatch):
    week1 = upload(client).json()["id"]
    ready(client, week1)
    client.post(f"/api/retreats/{week1}/days/1/build", json={"voice": "en-US-AvaMultilingualNeural"})
    ready(client, week1, lambda b: b["days"]["1"]["status"] != "building")
    week2 = upload(client).json()["id"]
    ready(client, week2)

    seen = {}
    real_plan, real_heart = llm.plan_retreat, llm.write_heart

    async def spy_plan(source, filename, instructions, meter, series_text=""):
        seen["plan"] = series_text
        return await real_plan(source, filename, instructions, meter, series_text)

    async def spy_heart(context, instructions, words, meter, series_text=""):
        seen["heart"] = series_text
        return await real_heart(context, instructions, words, meter, series_text)

    monkeypatch.setattr(llm, "plan_retreat", spy_plan)
    monkeypatch.setattr(llm, "write_heart", spy_heart)

    # Sent newest first; stored oldest first.
    week3 = upload(client, series=f"{week2},{week1}").json()
    assert week3["series"] == [week1, week2]
    body = ready(client, week3["id"])
    assert body["series_info"]["retreats"] == 2
    assert "=== Week 1 of 2" in seen["plan"] and "=== Week 2 of 2" in seen["plan"]
    assert "Reflection for the heart: Stub heart" in seen["plan"]  # week 1's built day comes along in full

    client.post(f"/api/retreats/{week3['id']}/days/1/build", json={"voice": "en-US-AvaMultilingualNeural"})
    ready(client, week3["id"], lambda b: b["days"]["1"]["status"] != "building")
    assert "=== Week 1 of 2" in seen["heart"]
    assert week3["id"] and [r["series"] for r in client.get("/api/retreats").json()["retreats"] if r["id"] == week3["id"]] == [[week1, week2]]


def test_series_rejects_unknown_or_foreign_retreats(client):
    mine = upload(client).json()["id"]
    ready(client, mine)
    assert upload(client, series="not-a-real-id").status_code == 400
    stored = store.rows / f"{mine}.json"
    data = json.loads(stored.read_text())
    data["user_id"] = "someone-else"
    stored.write_text(json.dumps(data))
    pipeline.active.clear()
    r = upload(client, series=mine)
    assert r.status_code == 400 and "wasn't found" in r.json()["error"]["message"]


def test_claude_gets_the_series_as_a_cached_system_block():
    system = llm._system("Write the reflection.", "<series>…</series>")
    assert system[0]["cache_control"] == {"type": "ephemeral"} and system[0]["text"].startswith(series.INSTRUCTIONS)
    assert system[1]["text"] == "Write the reflection."
    assert llm._system("Plain.", "") == "Plain."


def test_background_joins_the_cached_series_block():
    blocks = llm._with_background(llm._system("Write.", "<series>…</series>"))
    assert blocks[0]["text"].startswith("Background for this work") and blocks[0]["cache_control"]
    assert llm._with_background("Plain.").endswith("Plain.")
