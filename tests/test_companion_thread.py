"""One continuous conversation with the companion: spoken, typed, or both, over several
sittings, with a running summary once it gets long."""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import auth, talk, talk_turns
from app.storage import LocalStore


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def local(monkeypatch, tmp_path):
    store = LocalStore(tmp_path)
    monkeypatch.setattr(talk, "store", store)
    monkeypatch.setattr(talk, "providers", lambda: {"turns": talk_turns.provider_info()})
    monkeypatch.setattr(talk_turns, "check_brain", lambda brain, full: "free")
    asks = []

    async def think(brain, system, ask):
        asks.append(ask)
        return f"Reply {len(asks)}.", 0.0
    monkeypatch.setattr(talk_turns, "_think", think)
    talk._sessions.clear()
    return asks


def test_a_conversation_carries_on_across_sittings_and_modes(local):
    user = auth.User("u-thread", "a@b.c")
    first = run(talk.start(user, None, "", "", "turns", "", None))
    run(talk.turn(user, first["session_id"], "I prayed with the psalm."))  # spoken or typed: the same session
    run(talk.end(user, first["session_id"], 0, ""))
    second = run(talk.start(user, None, "", "", "turns", "", None))
    assert second["continued"] and [p["who"] for p in second["previous"]][:2] == ["companion", "you"]
    assert "come back to continue" in local[-1]  # welcomed back, not greeted afresh
    run(talk.turn(user, second["session_id"], "Now I'm typing."))
    run(talk.end(user, second["session_id"], 0, ""))
    history = run(talk.load_history(user.id))
    assert len(history["conversations"]) == 1  # one conversation, not two
    text = history["conversations"][0]["transcript"]
    assert "I prayed with the psalm." in text and "Now I'm typing." in text


def test_an_old_conversation_is_not_continued(local):
    user = auth.User("u-old", "a@b.c")
    old = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    run(talk.save_history(user.id, {"memory": "", "conversations": [
        {"id": "c1", "started_at": old, "ended_at": old, "transcript": "You: hello\nCompanion: hi"}]}))
    assert not run(talk.start(user, None, "", "", "turns", "", None))["continued"]


def test_starting_fresh_is_always_possible(local):
    user = auth.User("u-fresh", "a@b.c")
    first = run(talk.start(user, None, "", "", "turns", "", None))
    run(talk.end(user, first["session_id"], 0, ""))
    assert not run(talk.start(user, None, "", "", "turns", "", None, carry_on=False))["continued"]


def test_a_long_conversation_is_summarized_and_kept_whole(local, monkeypatch):
    async def summarize(session, text):
        return "They spoke of rest and of their father.", 0.0
    monkeypatch.setattr(talk_turns, "_summarize", summarize)
    user = auth.User("u-long", "a@b.c")
    s = run(talk.start(user, None, "", "", "turns", "", None))
    for i in range(30):
        run(talk.turn(user, s["session_id"], f"Line {i}: " + "x" * 600))
    session = talk._sessions[s["session_id"]]
    window = sum(len(w) for _, w in session["turns"])
    assert session["summary"] and window <= talk_turns.SUMMARY_OVER + 700  # never much past the limit
    assert "Earlier in this conversation" in local[-1]
    assert len(session["log"]) == 61  # nothing lost from the saved record


def test_the_companion_knows_how_the_person_is_talking(local, monkeypatch):
    systems = []

    async def think(brain, system, ask):
        systems.append((system, ask))
        return "Yes.", 0.0
    monkeypatch.setattr(talk_turns, "_think", think)
    user = auth.User("u-modes", "a@b.c")
    s = run(talk.start(user, None, "", "", "turns", "", None, mode="text"))
    assert "This conversation is written" in systems[-1][0]
    run(talk.turn(user, s["session_id"], "Hello in text.", "text"))
    run(talk.turn(user, s["session_id"], "Now I'm typing but listening.", "listen"))
    system, ask = systems[-1]
    assert "spoken aloud to them" in system and "switched to typing" in ask
    run(talk.end(user, s["session_id"], 0, ""))
    saved = run(talk.load_history(user.id))["conversations"][0]["transcript"]
    assert "[They've switched to typing" in saved
