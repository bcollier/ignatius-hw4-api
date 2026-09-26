"""The Costs page: each retreat by part and by company, from the call log and the voices."""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import costs, main, tts
from app.auth import User, current_user
from app.storage import store

DOCX = Path(__file__).parent.parent / "samples" / "three-days-called-by-name-web.docx"
VOICES = {"guide": "en-US-AvaMultilingualNeural", "reading": "en-US-AndrewMultilingualNeural",
          "heart": "en-US-AndrewMultilingualNeural", "deep": "en-US-ChristopherNeural"}


def test_report_groups_by_part_and_company():
    retreat = {"id": "r1", "created_at": 1, "model": "m", "plan": {"title": "T"},
               "days": {"1": {"tracks": {"heart": {"status": "ready", "voice": "en-US-AndrewMultilingualNeural", "characters": 900}}}}}
    rows = [
        {"retreat_id": "r1", "purpose": "plan", "provider": "openrouter", "usd": 0.5, "input_tokens": 100},
        {"retreat_id": "r1", "purpose": "deep", "provider": "openrouter", "usd": 1.0, "web_searches": 3},
        {"retreat_id": "r1", "purpose": "research", "provider": "exa", "usd": 0.007},
        {"retreat_id": "r1", "purpose": "talk", "provider": "xai", "usd": 0.08, "duration_ms": 60000},
        {"retreat_id": None, "purpose": "talk_memory", "provider": "openrouter", "usd": 0.01},
        {"retreat_id": "gone", "purpose": "heart", "provider": "jetstream", "usd": 0.0},
    ]
    r = costs.report([retreat], rows)
    one = r["retreats"][0]
    sections = {s["name"]: s for s in one["sections"]}
    assert list(sections) == ["Planning", "Deep dive", "Research", "Voices", "Talk it over"]
    assert sections["Deep dive"]["searches"] == 3 and sections["Talk it over"]["seconds"] == 60
    assert sections["Voices"]["free_characters"] == 900 and sections["Voices"]["usd"] == 0
    vendors = {v["name"]: v["usd"] for v in one["vendors"]}
    assert vendors["Anthropic (via OpenRouter)"] == 1.5 and vendors["Exa"] == 0.007 and "Microsoft voices (free)" in vendors
    assert one["total_usd"] == pytest.approx(1.587) and r["other"]["usd"] == pytest.approx(0.01)
    assert r["total_usd"] == pytest.approx(1.597)


def test_costs_endpoint(monkeypatch):
    async def fake_synthesize(text, voice, out_path):
        out_path.write_bytes(b"ID3")
        return 2.0

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    with TestClient(main.app) as client:
        main.app.dependency_overrides[current_user] = lambda: User("cost-user", "c@x.y")
        with open(DOCX, "rb") as f:
            rid = client.post("/api/retreats", files={"file": ("d.docx", f)}, data={"options": json.dumps({"voices": VOICES})}).json()["id"]
        for _ in range(200):
            if client.get(f"/api/retreats/{rid}").json()["status"] == "ready":
                break
            time.sleep(0.05)
        body = client.get("/api/costs").json()
        main.app.dependency_overrides.clear()
    mine = next(r for r in body["retreats"] if r["id"] == rid)
    # Test mode makes no model calls, so only the (free) voices show up here.
    voices = next(s for s in mine["sections"] if s["name"] == "Voices")
    assert voices["free_characters"] > 0 and mine["total_usd"] == 0
    assert "prices" in body and "elevenlabs_usd_per_1k_chars" in body["prices"]
