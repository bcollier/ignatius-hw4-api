"""Your own Examen: full accounts only; written and recorded in the background."""

import asyncio

from fastapi.testclient import TestClient

from app import main, my_examen, tts
from app.auth import User, current_user

SESSION = {"title": "Your Examen", "summary": "s", "segments": [
    {"kind": "speak", "step": "Presence", "text": "Good evening.", "seconds": 0, "question": "", "prompts": [], "why": ""},
    {"kind": "silence", "step": "Presence", "text": "", "seconds": 30, "question": "Breathe", "prompts": [], "why": ""},
]}


def test_free_accounts_cannot_make_one():
    with TestClient(main.app) as client:
        main.app.dependency_overrides[current_user] = lambda: User("ex-free", "f@b.c", full=False)
        assert client.post("/api/practice/examen", json={"days": "I teach."}).status_code == 403
        assert client.get("/api/practice/examen").json()["status"] == "none"
        main.app.dependency_overrides.clear()


def test_it_is_written_recorded_and_served(monkeypatch):
    async def write(days):
        assert "I teach" in days
        return {**SESSION, "segments": [dict(g) for g in SESSION["segments"]], "id": "my-examen"}, 0.1

    async def synthesize(text, voice, out_path):
        assert voice == "en-GB-RyanNeural"
        out_path.write_bytes(b"mp3")
        return 1.5

    monkeypatch.setattr(my_examen, "_write", write)
    monkeypatch.setattr(tts, "synthesize", synthesize)
    with TestClient(main.app) as client:
        main.app.dependency_overrides[current_user] = lambda: User("ex-full", "g@b.c")
        assert client.post("/api/practice/examen", json={"voice": "loud"}).status_code == 400
        r = client.post("/api/practice/examen", json={"days": "I teach; we drive to school.", "voice": "standard"})
        assert r.json()["status"] == "making"
        for _ in range(50):
            state = client.get("/api/practice/examen").json()
            if state["status"] != "making":
                break
            asyncio.run(asyncio.sleep(0.05))
        assert state["status"] == "ready", state
        spoken = state["session"]["segments"][0]["audio"]
        assert spoken["seconds"] == 1.5 and spoken["file"]
        assert state["session"]["voices"] == {"standard": "Ryan, Microsoft (free)"}
        main.app.dependency_overrides.clear()


def test_an_examen_cut_off_by_a_restart_starts_again(monkeypatch):
    started = []

    async def make(user_id, email, state):
        started.append(state["days"])

    monkeypatch.setattr(my_examen, "_make", make)
    user = "ex-restart"
    old = {"status": "making", "started": my_examen.BOOTED - 60, "days": "I teach.", "voice": "standard"}
    asyncio.run(my_examen._save(user, old))
    state = asyncio.run(my_examen.load(user))
    assert state["status"] == "making" and state["restarts"] == 1
    asyncio.run(asyncio.sleep(0))
    my_examen._running.discard(user)
    asyncio.run(my_examen._save(user, {**old, "restarts": my_examen.MAX_RESTARTS}))
    assert asyncio.run(my_examen.load(user))["status"] == "failed"
