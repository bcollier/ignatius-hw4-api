"""Security fixes from the September 27, 2026 audit (security-audit-2026-09-27), each
asserting the secure behavior the audit's reproductions showed was missing."""

import asyncio
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth, config, demos, llm, main, pipeline, talk
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
    monkeypatch.setattr(pipeline, "load", AsyncMock(return_value=example))
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


# ---------------------------------------------------------------- F10: no side effects before access is known

def test_someone_elses_stale_retreat_is_not_resumed_before_access_is_denied(monkeypatch):
    from fastapi import HTTPException

    from app.access import my_retreat
    stale = {"id": "r1", "user_id": "owner", "status": "planning", "heartbeat": 0}
    monkeypatch.setattr(pipeline, "load", AsyncMock(return_value=stale))
    resume = AsyncMock()
    monkeypatch.setattr(pipeline, "_resume", resume)
    with pytest.raises(HTTPException):
        run(my_retreat("r1", auth.User("stranger", "")))
    resume.assert_not_awaited()


def test_retreat_ids_cannot_reach_outside_the_store(tmp_path):
    store = LocalStore(tmp_path)
    (tmp_path / "outside.json").write_text('{"secret": true}')
    assert run(store.load("../outside")) is None
    assert run(pipeline.load("../outside")) is None


# ---------------------------------------------------------------- F08: deleting deletes

def test_deleting_a_retreat_removes_its_whole_folder_and_log_bodies(tmp_path):
    store = LocalStore(tmp_path)
    retreat = {"id": "r-del", "user_id": "u1", "images": [], "days": {}}
    run(store.save(retreat))
    for name in ("source.json", "scan1.png", "day1_heart.mp3"):
        run(store.put_file(f"u1/r-del/{name}", b"x", "application/octet-stream"))
    run(store.put_file("u1/other/keep.mp3", b"x", "audio/mpeg"))
    run(store.log_llm_call({"retreat_id": "r-del", "user_id": "u1", "purpose": "heart", "usd": 0.02,
                            "request": {"system": "SECRET_PROMPT"}, "response_text": "SECRET_REPLY", "response": {}}))
    run(store.delete(retreat))
    assert run(store.list_prefix("u1/r-del/")) == []
    assert run(store.list_prefix("u1/other/")) == ["u1/other/keep.mp3"]
    log = (tmp_path / "llm_calls.jsonl").read_text()
    assert "SECRET" not in log and '"usd": 0.02' in log  # the cost stays for the Costs page


def test_forgetting_conversations_erases_their_log_copies_and_memory_updates_cant_restore_them(monkeypatch, tmp_path):
    store = LocalStore(tmp_path)
    monkeypatch.setattr(talk, "store", store)
    run(store.log_llm_call({"user_id": "u2", "purpose": "talk", "request": {"system": "x"}, "response_text": "PRIVATE_TALK"}))
    run(store.log_llm_call({"user_id": "u2", "purpose": "heart", "request": {"system": "x"}, "response_text": "A reflection"}))
    history = {"memory": "", "conversations": [{"started_at": "2026-09-01", "transcript": "t" * 9000} for _ in range(8)]}
    run(talk.save_history("u2", history))

    async def slow_condense(*args):
        await talk.clear_history("u2")  # they press Forget while the memory is being written
        return "A memory of everything"
    monkeypatch.setattr(llm, "condense_text", slow_condense)
    run(talk._remember("u2", True))
    assert run(talk.load_history("u2"))["conversations"] == []
    assert run(talk.load_history("u2"))["memory"] == ""
    log = (tmp_path / "llm_calls.jsonl").read_text()
    assert "PRIVATE_TALK" not in log and "A reflection" in log


# ---------------------------------------------------------------- F09: sign-in codes

def test_a_flood_of_wrong_codes_never_blocks_a_valid_one_and_spoofed_addresses_dont_help(monkeypatch):
    from app.routes import handoff
    monkeypatch.setattr(auth, "enabled", lambda: True)

    async def token(email):
        return "hashed"
    monkeypatch.setattr(handoff, "_sign_in_token", token)
    handoff._codes.clear()
    handoff._wrong.clear()
    with TestClient(main.app) as client:
        main.app.dependency_overrides[auth.current_user] = lambda: auth.User("u1", "a@b.c")
        first = client.post("/api/handoff").json()["code"]
        code = client.post("/api/handoff").json()["code"]
        assert first not in handoff._codes  # one code per account
        main.app.dependency_overrides.clear()
        # 300 wrong guesses from 300 real addresses (the edge's header, which callers can't set)
        for i in range(300):
            client.post("/api/handoff/redeem", json={"code": "00000000"}, headers={"cf-connecting-ip": f"10.0.{i // 250}.{i % 250}"})
        # a caller rotating its own X-Forwarded-For claim is still one address to the edge
        statuses = {client.post("/api/handoff/redeem", json={"code": "00000000"},
                                headers={"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": f"198.51.100.{i}"}).status_code
                    for i in range(handoff.MAX_TRIES + 2)}
        assert 429 in statuses
        ok = client.post("/api/handoff/redeem", json={"code": code}, headers={"cf-connecting-ip": "192.0.2.1"})
        assert ok.status_code == 200 and ok.json()["token_hash"] == "hashed"


# ---------------------------------------------------------------- F04: uploads can't balloon

def test_an_oversized_upload_is_refused_before_it_is_read(monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    from app.body_limit import BodyLimit
    received = []

    async def app(scope, receive, send):
        while True:
            m = await receive()
            received.append(len(m.get("body", b"")))
            if not m.get("more_body"):
                break
    limited = BodyLimit(app, max_bytes=1024 * 1024)
    sent = []

    async def send(message):
        sent.append(message)

    chunks = iter([{"type": "http.request", "body": b"x" * 600_000, "more_body": True}] * 3)

    async def receive():
        return next(chunks)
    run(limited({"type": "http", "headers": []}, receive, send))  # no Content-Length: counted as it streams
    assert sent[0]["status"] == 413 and len(received) == 1
    sent.clear()
    run(limited({"type": "http", "headers": [(b"content-length", b"99999999")]}, receive, send))
    assert sent[0]["status"] == 413


def test_word_files_that_would_unpack_to_something_huge_are_refused():
    import io
    import zipfile

    from app.extract import ExtractError, extract
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * (50 * 1024 * 1024))  # 50 MB of zeros, a few KB zipped
    with pytest.raises(ExtractError):
        extract("bomb.docx", buf.getvalue())


def test_huge_images_are_refused_before_decoding(monkeypatch):
    from app import extract as ex
    monkeypatch.setattr(ex, "_image_size", lambda data: (20_000, 20_000))
    with pytest.raises(ex.ExtractError):
        ex.photo_image(b"not decoded")


def test_scanned_pages_are_drawn_within_the_pixel_budget():
    from types import SimpleNamespace

    from app import extract as ex
    poster = SimpleNamespace(rect=SimpleNamespace(width=72 * 60, height=72 * 40))  # a 60 x 40 inch page
    dpi = ex._render_dpi(poster)
    assert (60 * dpi) * (40 * dpi) <= ex.MAX_RENDER_PIXELS
    letter = SimpleNamespace(rect=SimpleNamespace(width=612, height=792))
    assert ex._render_dpi(letter) == 150


# ---------------------------------------------------------------- F03: limits on work

def test_a_person_can_only_start_so_much(monkeypatch, tmp_path):
    from fastapi import HTTPException

    from app import quotas
    monkeypatch.setattr(quotas, "store", LocalStore(tmp_path))
    monkeypatch.setattr(pipeline, "active", {})
    guest = auth.User("guest-q", "", full=False, anonymous=True)
    for _ in range(quotas.LIMITS["free"]["per_day"]):
        run(quotas.admit(guest))
    with pytest.raises(HTTPException) as day:
        run(quotas.admit(guest))
    assert day.value.status_code == 429
    pipeline.active["r-busy"] = {"user_id": "full-q"}
    full = auth.User("full-q", "a@b.c")
    for i in range(quotas.LIMITS["full"]["at_once"] - 1):
        pipeline.active[f"r{i}"] = {"user_id": "full-q"}
    with pytest.raises(HTTPException) as at_once:
        run(quotas.admit(full, count=False))
    assert at_once.value.status_code == 429
    monkeypatch.setattr(pipeline, "active", {f"x{i}": {"user_id": f"u{i}"} for i in range(quotas.MAX_ACTIVE)})
    with pytest.raises(HTTPException) as busy:
        run(quotas.admit(auth.User("someone", "s@b.c")))
    assert busy.value.status_code == 503


def test_error_reports_are_rate_limited_per_address():
    from app.routes import client_errors
    client_errors._recent.clear()
    client_errors._all.clear()
    kept = sum(client_errors._allowed("203.0.113.5") for _ in range(50))
    assert kept == client_errors.PER_ADDRESS


# ---------------------------------------------------------------- the sign-in cache

def test_the_sign_in_cache_keeps_no_tokens_and_honours_expiry(monkeypatch):
    import base64
    import json as _json
    monkeypatch.setattr(auth, "_cache", {})
    past = base64.urlsafe_b64encode(_json.dumps({"exp": time.time() - 10}).encode()).decode().rstrip("=")
    token = f"h.{past}.s"
    auth._remember("key1", auth.User("u", "a@b.c"), token)
    assert token not in auth._cache and all(len(k) < 100 for k in auth._cache)
    assert auth._cache["key1"][1] <= time.time()  # already expired: it won't be used


# ---------------------------------------------------------------- private notes stay out of web searches

def test_search_queries_are_written_without_the_persons_notes(monkeypatch):
    from app import jetstream, prompts, search
    seen = {}

    async def complete(model, system, text, meter, **kw):
        seen["notes"] = prompts.PERSON.get()
        return "query one"

    async def fake_search(queries, provider):
        return search.Research()
    monkeypatch.setattr(jetstream, "complete", complete)
    monkeypatch.setattr(search, "search", fake_search)

    async def job():
        prompts.PERSON.set("I am recovering from an illness")
        await llm._server_research("muse", "Retreat: x\nDay 1: y\nSource reference: Psalm 23", type("M", (), {"searches": 0, "usd": 0})(), "all")
        return prompts.PERSON.get()
    after = run(job())
    assert seen["notes"] == "" and after == "I am recovering from an illness"
