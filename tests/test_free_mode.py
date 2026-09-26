"""Free mode (people not on ALLOWED_EMAILS) and the Jetstream client."""

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, jetstream, main, pricing, tts
from app.auth import User, current_user

SAMPLE = Path(__file__).parent.parent / "samples" / "loose-passages-web.pdf"
GUEST = User("11111111-1111-1111-1111-111111111111", "", full=False, anonymous=True)


@pytest.fixture
def guest(monkeypatch):
    monkeypatch.setattr(config, "JETSTREAM_API_KEY", "test-token")
    monkeypatch.setattr(config, "FREE_MODE", True)

    async def fake_synthesize(text, voice, out_path):
        out_path.write_bytes(b"ID3")
        return 1.0

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    main.app.dependency_overrides[current_user] = lambda: GUEST
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


def upload(client, **form):
    with open(SAMPLE, "rb") as f:
        return client.post("/api/retreats", files={"file": ("s.pdf", f)}, data=form)


def ready(client, rid):
    for _ in range(100):
        body = client.get(f"/api/retreats/{rid}").json()
        if body["status"] != "planning":
            return body
        time.sleep(0.05)


def test_guest_gets_jetstream_and_a_retreat_cap(guest):
    me = guest.get("/api/me").json()
    assert me["mode"] == "free" and me["anonymous"] and me["max_retreats"] == config.FREE_MAX_RETREATS

    r = upload(guest)
    assert r.status_code == 202 and r.json()["model"] == "jetstream/llama-4-scout"
    assert upload(guest, model="anthropic/claude-opus-5").status_code == 403

    for _ in range(config.FREE_MAX_RETREATS - 1):
        assert upload(guest).status_code == 202
    r = upload(guest)
    assert r.status_code == 403 and "Free mode keeps up to" in r.json()["error"]["message"]


def test_guest_builds_only_with_free_voices_and_jetstream(guest):
    listed = guest.get("/api/retreats").json()["retreats"]
    rid = listed[0]["id"] if listed else upload(guest).json()["id"]
    ready(guest, rid)
    url = f"/api/retreats/{rid}/days/1/build"
    assert guest.post(url, json={"voice": "nPczCjzI2devNBz1zQrb"}).status_code == 403  # ElevenLabs
    assert guest.post(url, json={"model": "anthropic/claude-opus-5"}).status_code == 403
    r = guest.post(url, json={"voice": "en-US-AvaMultilingualNeural", "model": "jetstream/muse-glimmer"})
    assert r.status_code == 202


def test_options_list_jetstream_models_as_free(guest):
    models = {m["id"]: m for m in guest.get("/api/options").json()["models"]}
    assert models["jetstream/llama-4-scout"]["free"] and models["jetstream/llama-4-scout"]["input_per_m"] == 0


def test_jetstream_client_retries_without_images(monkeypatch):
    monkeypatch.setattr(config, "JETSTREAM_API_KEY", "test-token")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.url.path == "/api/chat/completions"
        if isinstance(body["messages"][1]["content"], list):
            return httpx.Response(400, json={"error": "images not supported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "<script>Hello.</script>"}}], "usage": {"prompt_tokens": 12, "completion_tokens": 3}})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    meter = pricing.Meter("jetstream/llama-4-scout", {})
    with pytest.raises(jetstream.ImagesRejected):
        asyncio.run(jetstream.complete("llama-4-scout", "sys", "text", meter, images=[(b"x", "image/jpeg")]))
    reply = asyncio.run(jetstream.complete("llama-4-scout", "sys", "text", meter))
    assert reply == "<script>Hello.</script>" and meter.summary()["input_tokens"] == 12 and meter.summary()["usd"] == 0
