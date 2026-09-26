"""Watching a retreat being made: steps, calls and recordings in order, and .txt uploads."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app import main, tts
from app.auth import User, current_user
from app.routes.build_log import log_row

VOICES = {"guide": "en-US-AvaMultilingualNeural", "reading": "en-US-AndrewMultilingualNeural",
          "heart": "en-US-AndrewMultilingualNeural", "deep": "en-US-ChristopherNeural"}
TEXT = b"""Two days of prayer.

Day 1: Called by name
Isaiah 43:1. But now Yahweh who created you, Jacob, says: Don't be afraid, for I have redeemed you. I have called you by your name. You are mine.

Day 2: Be still
Psalm 46:10. Be still, and know that I am God.
"""


@pytest.fixture
def client(monkeypatch):
    async def fake_synthesize(text, voice, out_path):
        out_path.write_bytes(b"ID3")
        return 2.0

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


def test_txt_upload_and_the_build_log(client):
    main.app.dependency_overrides[current_user] = lambda: User("log-user", "l@x.y")
    r = client.post("/api/retreats", files={"file": ("two-days.txt", TEXT)}, data={"options": json.dumps({"voices": VOICES})})
    assert r.status_code == 202, r.text
    rid = r.json()["id"]
    seen, after = [], 0
    for _ in range(300):
        page = client.get(f"/api/retreats/{rid}/log", params={"after": after}).json()
        seen += page["rows"]
        after = seen[-1]["id"] if seen else after
        if not page["busy"] and seen:
            break
        time.sleep(0.03)
    ids = [x["id"] for x in seen]
    assert ids == sorted(ids) and len(ids) == len(set(ids))  # in order, each once
    steps = [x["response"] for x in seen if x["purpose"] == "step"]
    assert steps[0].startswith("Read two-days.txt") and "Next: planning" in steps[1]
    assert any(s.startswith("Planned") for s in steps) and steps[-1].startswith("All days made")
    voices = [x for x in seen if x["purpose"] == "voice"]
    bad = [v for v in voices if not (v["provider"] == "microsoft" and v["prompt"])]
    assert voices and not bad, [(v["provider"], v["model"], v["prompt"][:40], v["response"][:60]) for v in bad]
    full = client.get(f"/api/retreats/{rid}/log", params={"full": True}).json()["rows"]
    assert len(full) == len(seen) and "details" in full[0]


def test_private_notes_are_hidden_from_others():
    row = {"id": 1, "purpose": "heart", "request": {"system": "Background.\n<about_the_person>I am grieving.</about_the_person>\nWrite.",
                                                   "messages": [{"role": "user", "content": "Day 1"}]}, "response_text": "x"}
    mine = log_row(row, full=True, owner=True)
    theirs = log_row(row, full=True, owner=False)
    assert "grieving" in mine["system"] and "grieving" not in theirs["system"] and "[private]" in theirs["system"]
    short = log_row({**row, "response_text": "y" * 5000}, full=False, owner=True)
    assert len(short["response"]) < 1300 and "system" not in short and short["system_chars"] > 0
