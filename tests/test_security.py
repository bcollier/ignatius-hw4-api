"""Security fixes from the September 27, 2026 audit (security-audit-2026-09-27), each
asserting the secure behavior the audit's reproductions showed was missing."""

import asyncio
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth, config, demos, main, pipeline, talk
from app.routes import build_log
from app.storage import LocalStore, SupabaseStore


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean():
    talk._sessions.clear()
    yield
    main.app.dependency_overrides.clear()
    talk._sessions.clear()


# ---------------------------------------------------------------- F01: example logs

def test_a_guest_cannot_read_someone_elses_conversation_through_an_example_log(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    example = {"id": "shared-demo", "user_id": "demo-owner", "status": "ready", "images": [], "days": {}}
    monkeypatch.setattr(pipeline, "get", AsyncMock(return_value=example))
    monkeypatch.setattr(demos, "registry", AsyncMock(return_value={"shared-demo": {"kind": "free"}}))
    monkeypatch.setattr(demos, "load_state", AsyncMock(return_value={}))
    monkeypatch.setattr(build_log, "store", store)
    run(store.log_llm_call({"retreat_id": "shared-demo", "user_id": "victim", "purpose": "talk",
                            "request": {"system": "<wants>VICTIM_NOTES</wants>", "messages": []},
                            "response_text": "VICTIM_CONVERSATION", "response": {}}))
    run(store.log_llm_call({"retreat_id": "shared-demo", "user_id": "demo-owner", "purpose": "talk",
                            "request": {"system": "OWNER_TALK", "messages": []}, "response_text": "OWNER_CONVERSATION"}))
    run(store.log_llm_call({"retreat_id": "shared-demo", "user_id": "demo-owner", "purpose": "heart",
                            "request": {"system": "the heart prompt", "messages": []}, "response_text": "A reflection.",
                            "response": {"reasoning": "PRIVATE_REASONING"}}))
    main.app.dependency_overrides[auth.current_user] = lambda: auth.User("stranger", "", full=False, anonymous=True)
    with TestClient(main.app) as client:
        body = client.get("/api/retreats/shared-demo/log?full=true").text
    assert "A reflection." in body  # how the example was made is still shown
    for secret in ("VICTIM_CONVERSATION", "VICTIM_NOTES", "OWNER_CONVERSATION", "PRIVATE_REASONING"):
        assert secret not in body


def test_a_talk_about_an_example_is_not_filed_under_the_example():
    user = auth.User("guest-1", "", full=False, anonymous=True)
    example_view = {"id": "shared-demo", "user_id": "demo-owner", "read_only": True}
    own = {"id": "mine", "user_id": "guest-1"}
    assert talk._own_retreat_id(example_view, user) is None
    assert talk._own_retreat_id(own, user) == "mine"


# ---------------------------------------------------------------- F02: voice billing

def live(owner="alice", provider="openai", started_ago=30, max_seconds=60, full=False):
    return dict(user_id=owner, email="x@example.invalid", provider=provider, voice="marin",
                started=time.time() - started_ago, max=max_seconds, retreat_id=None, log_retreat_id=None,
                retreat_title=None, instructions="synthetic", full=full)


def test_the_server_charges_the_time_it_measured_not_what_the_browser_reports(monkeypatch):
    usage, hangup = AsyncMock(), AsyncMock()
    monkeypatch.setattr(talk, "add_usage", usage)
    monkeypatch.setattr(talk, "_hang_up", hangup)
    monkeypatch.setattr(talk.llm_log, "record", AsyncMock())
    talk._sessions["s1"] = live(started_ago=30)
    run(talk.end(auth.User("alice", "", full=False), "s1", 0, ""))  # the browser says zero
    charged = usage.await_args.args[1]
    assert 29 <= charged <= 31
    hangup.assert_awaited_once_with("s1")  # and the live call is really hung up


def test_someone_else_cannot_end_a_call():
    talk._sessions["s2"] = live(owner="alice")
    run(talk.end(auth.User("bob", "", full=False), "s2", 0, ""))
    assert "s2" in talk._sessions


def test_a_call_the_browser_never_ends_is_charged_at_the_limit(monkeypatch):
    usage = AsyncMock()
    monkeypatch.setattr(talk, "add_usage", usage)
    monkeypatch.setattr(talk, "_hang_up", AsyncMock())
    monkeypatch.setattr(talk.llm_log, "record", AsyncMock())
    talk._sessions["s3"] = live(started_ago=90, max_seconds=60)
    run(talk._finish_at_limit("s3", 0))
    assert usage.await_args.args[1] == 60 and "s3" not in talk._sessions


def test_one_live_call_at_a_time_and_grok_is_premium_only(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "synthetic")
    monkeypatch.setattr(config, "XAI_API_KEY", "synthetic")
    monkeypatch.setattr(talk, "seconds_used_today", AsyncMock(return_value=0))
    monkeypatch.setattr(talk, "load_history", AsyncMock(return_value={"memory": "", "conversations": []}))
    monkeypatch.setattr(talk, "_openai_session", AsyncMock(side_effect=[{"session_id": "one", "sdp": "a"}, {"session_id": "two", "sdp": "a"}]))
    monkeypatch.setattr(talk, "_finish_at_limit", AsyncMock())
    user = auth.User("alice", "", full=False)
    run(talk.start(user, None, "", "", "openai", "marin", "offer"))
    with pytest.raises(talk.TalkError) as second:
        run(talk.start(user, None, "", "", "openai", "marin", "offer"))
    assert second.value.status == 409
    with pytest.raises(talk.TalkError) as grok:
        run(talk.start(auth.User("carol", "", full=False), None, "", "", "xai", "eve", None))
    assert grok.value.status == 403


# ---------------------------------------------------------------- F05, F06: fail closed

def test_half_set_up_sign_in_stops_the_server(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_URL", "https://synthetic.example.invalid")
    monkeypatch.setattr(config, "SUPABASE_SECRET_KEY", "")
    monkeypatch.setattr(config, "LOCAL_MODE", False)
    with pytest.raises(RuntimeError):
        auth.check_setup()


def test_no_sign_in_without_local_mode_stops_the_server_and_refuses_requests(monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    monkeypatch.setattr(config, "SUPABASE_SECRET_KEY", "")
    monkeypatch.setattr(config, "LOCAL_MODE", False)
    with pytest.raises(RuntimeError):
        auth.check_setup()
    with pytest.raises(Exception) as refused:
        run(auth.current_user(""))
    assert getattr(refused.value, "status_code", None) == 503


def test_a_public_bucket_stops_the_server(monkeypatch):
    store = SupabaseStore("https://synthetic.example.invalid", "synthetic", "retreats")
    monkeypatch.setattr(store.http, "get", AsyncMock(return_value=httpx.Response(200, json={"public": True})))
    with pytest.raises(RuntimeError):
        run(store.setup())
    monkeypatch.setattr(store.http, "get", AsyncMock(return_value=httpx.Response(200, json={"public": False})))
    run(store.setup())  # a private bucket is fine


# ---------------------------------------------------------------- public options

def test_the_public_options_dont_show_the_elevenlabs_balance():
    with TestClient(main.app) as client:
        options = client.get("/api/options").json()
    assert "balance" not in options["elevenlabs"]
