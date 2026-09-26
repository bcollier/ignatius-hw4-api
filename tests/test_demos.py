"""Example retreats everyone can pray (read only, progress kept per person) and the
research page."""

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import demos, llm, main, tts
from app.auth import User, current_user

DOCX = Path(__file__).parent.parent / "samples" / "three-days-called-by-name-web.docx"
VOICES = {"guide": "en-US-AvaMultilingualNeural", "reading": "en-US-AndrewMultilingualNeural",
          "heart": "en-US-AndrewMultilingualNeural", "deep": "en-US-ChristopherNeural"}
OWNER, VISITOR = User("demo-owner", "o@x.y"), User("demo-visitor", "", full=False, anonymous=True)


@pytest.fixture
def client(monkeypatch):
    async def fake_synthesize(text, voice, out_path):
        out_path.write_bytes(b"ID3")
        return 2.0

    real_deep = llm.write_deep

    async def deep_with_research(context, instructions, words, meter, search_provider=None, series_text=""):
        script, _, _ = await real_deep(context, instructions, words, meter, search_provider, series_text)
        meter.research = {"how": "server search", "service": "all", "queries": ["prodigal son father ran"],
                          "contributors": ["exa", "tavily"], "skipped": [],
                          "results": [{"title": "A", "url": "https://a", "content": "snippet", "service": "exa"}]}
        return script, ["A — https://a"], "all"

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    monkeypatch.setattr(llm, "write_deep", deep_with_research)
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


def as_user(user):
    main.app.dependency_overrides[current_user] = lambda: user


def made(client) -> str:
    as_user(OWNER)
    with open(DOCX, "rb") as f:
        rid = client.post("/api/retreats", files={"file": ("d.docx", f)},
                          data={"options": json.dumps({"voices": VOICES})}).json()["id"]
    for _ in range(200):
        if client.get(f"/api/retreats/{rid}").json()["status"] == "ready":
            return rid
        time.sleep(0.05)
    raise AssertionError("not ready")


def test_research_page(client):
    rid = made(client)
    body = client.get(f"/api/retreats/{rid}/research").json()
    day = body["days"][0]
    assert day["passage_text"] and day["research"]["queries"] == ["prodigal son father ran"]
    assert day["research"]["results"][0]["service"] == "exa" and day["cited"] == ["A — https://a"]
    assert day["research_service"] == "several services, combined"
    as_user(User("stranger", "s@x.y"))
    assert client.get(f"/api/retreats/{rid}/research").status_code == 404


def test_example_retreat_is_shared_but_progress_is_personal(client):
    rid = made(client)
    as_user(VISITOR)
    assert client.get(f"/api/retreats/{rid}").status_code == 404  # not an example yet
    asyncio.run(demos.register(rid, "Example retreat · free", "free"))

    lib = client.get("/api/retreats").json()
    assert lib["retreats"] == [] and lib["examples"][0]["id"] == rid and lib["examples"][0]["demo"]["kind"] == "free"
    view = client.get(f"/api/retreats/{rid}").json()
    assert view["read_only"] and "costs" not in view and "_viewer" not in view

    # The visitor prays, journals, listens and moves the start date: all kept for them only.
    client.post(f"/api/retreats/{rid}/days/1/prayed", json={"word": "Come home"})
    client.post(f"/api/retreats/{rid}/days/2/progress", json={"step": 3, "part": "heart", "parts_played": ["heart"]})
    assert client.patch(f"/api/retreats/{rid}", json={"start_date": "2026-09-01"}).status_code == 200
    assert client.patch(f"/api/retreats/{rid}", json={"title": "Mine now"}).status_code == 403
    view = client.get(f"/api/retreats/{rid}").json()
    assert view["days"]["1"]["journal"]["word"] == "Come home" and view["start_date"] == "2026-09-01"
    assert view["days"]["2"]["listening"]["parts_played"] == ["heart"]

    # Building, rebuilding and deleting stay with the owner.
    assert client.delete(f"/api/retreats/{rid}").status_code == 404
    assert client.post(f"/api/retreats/{rid}/days/1/build", json={"voices": VOICES}).status_code == 404

    # The account that built it sees it as an example too, with its own progress, and can't delete it here.
    as_user(OWNER)
    own = client.get(f"/api/retreats/{rid}").json()
    assert own["read_only"] and not own["days"]["1"].get("journal") and own["start_date"] != "2026-09-01"
    lib = client.get("/api/retreats").json()
    assert [r["id"] for r in lib["examples"]] == [rid] and rid not in [r["id"] for r in lib["retreats"]]
    assert client.delete(f"/api/retreats/{rid}").status_code == 409
    asyncio.run(demos.unregister(rid))
    assert client.get(f"/api/retreats/{rid}").json().get("read_only") is None  # an ordinary retreat again


def test_an_example_can_be_hidden_from_the_home_page(client):
    rid = made(client)
    asyncio.run(demos.register(rid, "Example retreat · free", "free"))
    as_user(VISITOR)
    client.post(f"/api/retreats/{rid}/days/1/prayed", json={"word": "Still"})
    assert client.post(f"/api/retreats/{rid}/hidden", json={"hidden": True}).json()["hidden"] is True
    lib = client.get("/api/retreats").json()
    assert lib["examples"] == [] and lib["hidden_examples"][0]["id"] == rid
    client.post(f"/api/retreats/{rid}/days/2/prayed", json={})  # praying it later doesn't unhide it
    assert client.get("/api/retreats").json()["examples"] == []
    client.post(f"/api/retreats/{rid}/hidden", json={"hidden": False})
    back = client.get("/api/retreats").json()
    assert back["examples"][0]["id"] == rid and back["hidden_examples"] == [] and back["examples"][0]["days_prayed"] == 2
    as_user(OWNER)
    own = made(client)
    assert client.post(f"/api/retreats/{own}/hidden", json={"hidden": True}).status_code == 400
    asyncio.run(demos.unregister(rid))
