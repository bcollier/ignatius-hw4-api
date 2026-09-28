"""SupabaseStore and sign-in checks against a fake Supabase (no network)."""

import asyncio
import json

import httpx

from app import auth, config
from app.storage import SupabaseStore


def fake_supabase(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers, request.content))
        if request.url.path.startswith("/storage/v1/object/sign/"):
            paths = json.loads(request.content)["paths"]
            return httpx.Response(200, json=[{"path": p, "signedURL": f"/object/sign/retreats/{p}?token=t"} for p in paths])
        if request.method == "GET" and request.url.path == "/rest/v1/retreats":
            return httpx.Response(200, json=[{"data": {"id": "r1", "user_id": "u1", "filename": "f.pdf", "created_at": 1, "status": "ready", "days": {}, "plan": {"title": "T"}}}])
        return httpx.Response(200, json={})

    return handler


def make_store(calls, key="sb_secret_abc"):
    store = SupabaseStore("https://proj.supabase.co", key, "retreats")
    store.http = httpx.AsyncClient(base_url=store.url, headers=store.http.headers, transport=httpx.MockTransport(fake_supabase(calls)))
    return store


def test_new_secret_key_goes_only_in_apikey_header():
    calls = []
    asyncio.run(make_store(calls).put_file("u1/r1/day1_reading.mp3", b"mp3", "audio/mpeg"))
    method, path, headers, _ = calls[0]
    assert (method, path) == ("POST", "/storage/v1/object/retreats/u1/r1/day1_reading.mp3")
    assert headers["apikey"] == "sb_secret_abc" and "authorization" not in headers
    assert headers["x-upsert"] == "true"


def test_signed_urls_are_absolute_and_cached():
    calls = []
    store = make_store(calls)
    urls = asyncio.run(store.urls(["u1/r1/image0.jpg"]))
    assert urls["u1/r1/image0.jpg"] == "https://proj.supabase.co/storage/v1/object/sign/retreats/u1/r1/image0.jpg?token=t"
    asyncio.run(store.urls(["u1/r1/image0.jpg"]))
    assert len(calls) == 1


def test_list_and_save():
    calls = []
    store = make_store(calls)
    listed = asyncio.run(store.list_for("u1"))
    assert listed[0]["title"] == "T"
    asyncio.run(store.save({"id": "r1", "user_id": "u1", "filename": "f.pdf", "created_at": 1, "status": "ready", "days": {}, "plan": None}))
    method, path, headers, body = calls[-1]
    assert (method, path) == ("POST", "/rest/v1/retreats") and "merge-duplicates" in headers["prefer"]
    assert json.loads(body)["user_id"] == "u1"


def test_email_allowlist(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_EMAILS", ["ben@collier.phd"])
    assert auth.email_allowed("Ben@Collier.phd".lower())
    assert not auth.email_allowed("stranger@example.com")
    monkeypatch.setattr(config, "ALLOWED_EMAILS", [])
    monkeypatch.delenv("EVERYONE_FULL", raising=False)
    assert not auth.email_allowed("anyone@example.com")  # an empty list lets no one spend by accident
    monkeypatch.setenv("EVERYONE_FULL", "1")
    assert auth.email_allowed("anyone@example.com")  # unless it's asked for by name
