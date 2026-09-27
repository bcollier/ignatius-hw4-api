"""Talk it over, taking turns: a chosen brain writes the replies, a free voice speaks them."""

from fastapi.testclient import TestClient

from app import main, talk, talk_turns
from app.auth import User, current_user


def fake_brains(full):
    return {"free": "Muse (free)", **({"anthropic/claude-fable-5.1": "Fable"} if full else {})}


def test_a_conversation_takes_turns_and_is_saved(monkeypatch):
    heard = []

    async def think(brain, system, ask):
        heard.append((brain, ask))
        return ("Good evening. What stayed with you today?" if "just started" in ask else "What did that feel like?"), 0.01

    async def speak(voice, text):
        return b"mp3"

    monkeypatch.setattr(talk_turns, "brains", fake_brains)
    monkeypatch.setattr(talk_turns, "_think", think)
    monkeypatch.setattr(talk_turns, "speak", speak)
    with TestClient(main.app) as client:
        main.app.dependency_overrides[current_user] = lambda: User("tt-free", "f@b.c", full=False)
        r = client.post("/api/talk/session", json={"provider": "turns", "brain": "anthropic/claude-fable-5.1"}).json()
        assert r["brain"] == "free" and r["greeting"].startswith("Good evening")  # free accounts get the free brain
        sid = r["session_id"]
        assert client.post("/api/talk/turn", json={"session_id": sid, "text": "The word immediately."}).json()["reply"] == "What did that feel like?"
        assert "Them: The word immediately." in heard[-1][1]
        assert client.post("/api/talk/speak", json={"session_id": sid, "text": "What did that feel like?"}).content == b"mp3"
        main.app.dependency_overrides[current_user] = lambda: User("tt-other", "o@b.c")
        assert client.post("/api/talk/turn", json={"session_id": sid, "text": "hi"}).status_code == 404  # not theirs
        main.app.dependency_overrides[current_user] = lambda: User("tt-free", "f@b.c", full=False)
        client.post("/api/talk/end", json={"session_id": sid, "seconds": 60, "transcript": "Companion: Good evening.\nYou: The word immediately."})
        history = client.get("/api/talk/history").json()
        assert history["conversations"][-1]["provider"] == "turns"
        main.app.dependency_overrides.clear()


def test_premium_accounts_can_choose_a_paid_brain(monkeypatch):
    async def think(brain, system, ask):
        return f"[{brain}]", 0.02

    monkeypatch.setattr(talk_turns, "brains", fake_brains)
    monkeypatch.setattr(talk_turns, "_think", think)
    with TestClient(main.app) as client:
        main.app.dependency_overrides[current_user] = lambda: User("tt-full", "p@b.c")
        r = client.post("/api/talk/session", json={"provider": "turns", "brain": "anthropic/claude-fable-5.1"}).json()
        assert r["brain"] == "anthropic/claude-fable-5.1" and r["greeting"] == "[anthropic/claude-fable-5.1]"
        assert talk._sessions[r["session_id"]]["usd"] == 0.02
        main.app.dependency_overrides.clear()
