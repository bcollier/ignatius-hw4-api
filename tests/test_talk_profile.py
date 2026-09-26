"""About me ("user info.md") and Talk it over (live conversation)."""

import asyncio
import io
import json
import time
from pathlib import Path

import docx
import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, llm, main, profile, talk, tts
from app.auth import User, current_user

VOICES = {"guide": "en-US-AvaMultilingualNeural", "reading": "en-US-AndrewMultilingualNeural",
          "heart": "en-US-AndrewMultilingualNeural", "deep": "en-US-ChristopherNeural"}


@pytest.fixture
def client(monkeypatch):
    async def fake_synthesize(text, voice, out_path):
        out_path.write_bytes(b"ID3")
        return 2.0

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    with TestClient(main.app) as c:
        yield c


def as_user(user):
    main.app.dependency_overrides[current_user] = lambda: user


@pytest.fixture(autouse=True)
def reset_overrides():
    yield
    main.app.dependency_overrides.clear()


# ---------------------------------------------------------------- about me

def test_profile_typed_short_and_long(client, monkeypatch):
    as_user(User("p-user-1", "a@b.c"))
    r = client.put("/api/profile", json={"about": "I teach and pray at dawn.", "companion_notes": "Be gentle; ask about my family."}).json()
    assert r["file"] == "user info.md" and r["about"].startswith("I teach") and not r["summarized"]
    assert r["companion_notes"].startswith("Be gentle")

    monkeypatch.setattr(config, "PROFILE_MAX_CHARS", 100)
    long_text = "My life. " * 200
    r = client.put("/api/profile", json={"about": long_text}).json()
    assert r["summarized"] and r["original_characters"] == len(long_text.strip()) and len(r["about"]) <= 600
    assert r["companion_notes"].startswith("Be gentle")  # untouched when not sent


def test_profile_upload_word_and_text(client):
    as_user(User("p-user-2", "a@b.c"))
    d = docx.Document()
    d.add_paragraph("I am a nurse working nights; Psalm 139 matters to me.")
    buf = io.BytesIO()
    d.save(buf)
    r = client.post("/api/profile/upload", files={"file": ("me.docx", buf.getvalue())}).json()
    assert "Psalm 139" in r["about"]
    r = client.post("/api/profile/upload", files={"file": ("me.md", b"# Me\nI walk the dog before prayer.")}).json()
    assert "walk the dog" in r["about"]
    assert client.post("/api/profile/upload", files={"file": ("x.pdf", b"not a pdf")}).status_code == 400


def test_notes_reach_every_model_call(client):
    as_user(User("p-user-3", "a@b.c"))
    client.put("/api/profile", json={"about": "I'm grieving my father."})
    async def job():
        await profile.use_for_job("p-user-3")  # set for this job only
        return llm._with_background("Write.")

    system = asyncio.run(job())
    assert "<about_the_person>" in system and "grieving my father" in system and system.index("<background>") < system.index("grieving")


# ---------------------------------------------------------------- talk it over

def fake_openai(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    seen = {}

    async def session(instructions, voice, sdp):
        seen["instructions"] = instructions
        seen["voice"] = voice
        return {"session_id": f"live_{time.time()}", "sdp": "v=0 answer"}

    async def no_hangup(session_id, seconds):
        return None

    monkeypatch.setattr(talk, "_openai_session", session)
    monkeypatch.setattr(talk, "_hang_up_later", no_hangup)
    return seen


def test_talk_off_without_keys(client):
    assert client.get("/api/options").json()["talk"]["enabled"] is False
    assert client.post("/api/talk/session", json={"sdp": "x"}).status_code == 400


def test_free_minute_and_memory(client, monkeypatch):
    seen = fake_openai(monkeypatch)
    as_user(User("t-free-1", "", full=False, anonymous=True))
    r = client.post("/api/talk/session", json={"provider": "openai", "voice": "vesper", "sdp": "v=0 offer",
                                               "local_time": "2026-09-26T21:40:00-04:00"}).json()
    assert r["sdp"] == "v=0 answer" and r["max_seconds"] == config.FREE_TALK_SECONDS and r["voice"] == "vesper"
    assert "first conversation" in seen["instructions"] and "(at night)" in seen["instructions"]
    assert "spiritual director" in seen["instructions"]  # modeled on, and says it isn't one
    talk._sessions[r["session_id"]]["started"] -= 60  # the call really lasted a minute
    client.post("/api/talk/end", json={"session_id": r["session_id"], "seconds": 58,
                                       "transcript": "You: I felt restless.\nCompanion: What was the restlessness like?"})
    hist = client.get("/api/talk/history").json()
    assert hist["conversations"][-1]["transcript"].startswith("You: I felt restless")
    # The day's free minute is used up.
    r = client.post("/api/talk/session", json={"provider": "openai", "sdp": "v=0"})
    assert r.status_code == 403 and "free conversation" in r.json()["error"]["message"]
    client.delete("/api/talk/history")
    assert client.get("/api/talk/history").json()["conversations"] == []


def test_companion_knows_what_is_new_since_last_talk(client, monkeypatch):
    seen = fake_openai(monkeypatch)
    as_user(User("t-full-1", "me@x.y"))
    with open(Path(__file__).parent.parent / "samples" / "three-days-called-by-name-web.docx", "rb") as f:
        rid = client.post("/api/retreats", files={"file": ("d.docx", f)},
                          data={"options": json.dumps({"voices": VOICES}), "start_date": "2026-09-20"}).json()["id"]
    for _ in range(200):
        if client.get(f"/api/retreats/{rid}").json()["status"] == "ready":
            break
        time.sleep(0.05)
    s = client.post("/api/talk/session", json={"provider": "openai", "sdp": "o", "retreat_id": rid}).json()
    assert s["max_seconds"] == config.TALK_MAX_SECONDS
    client.post("/api/talk/end", json={"session_id": s["session_id"], "seconds": 300, "transcript": "You: hello"})
    time.sleep(0.01)
    client.post(f"/api/retreats/{rid}/days/2/prayed", json={"word": "Here I am"})
    client.post("/api/talk/session", json={"provider": "openai", "sdp": "o", "retreat_id": rid,
                                           "local_time": "2026-09-27T07:10:00-04:00"})
    text = seen["instructions"]
    assert "Since your last conversation they have listened to or prayed Day 2" in text
    assert "earlier today" in text or "yesterday" in text or "days ago" in text
    assert "early in the morning" in text and "Here I am" in text and "You: hello" in text


def test_grok_session_gets_a_token(client, monkeypatch):
    monkeypatch.setattr(config, "XAI_API_KEY", "xai-test")
    real = httpx.AsyncClient

    def handler(request):
        if request.url.path == "/v1/realtime/client_secrets":
            assert request.headers["authorization"] == "Bearer xai-test"
            return httpx.Response(200, json={"value": "ek_123", "expires_at": 1})
        return httpx.Response(200, json={"voices": [{"voice_id": "eve", "name": "Eve"}, {"voice_id": "rex", "name": "Rex"}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    talk._xai_voice_cache.clear()
    as_user(User("t-full-2", "me@x.y"))
    opts = client.get("/api/options").json()["talk"]
    assert "xai" in opts["providers"] and "rex" in opts["xai_voices"]
    r = client.post("/api/talk/session", json={"provider": "xai", "voice": "rex"}).json()
    assert r["token"] == "ek_123" and r["ws_url"].startswith("wss://api.x.ai/v1/realtime")
    assert r["session"]["voice"] == "rex" and "prayer companion" in r["session"]["instructions"]


def test_notes_do_not_leak_between_jobs(client):
    as_user(User("p-user-4", "a@b.c"))
    client.put("/api/profile", json={"about": "Secret detail."})

    async def two_jobs():
        async def job(uid):
            await profile.use_for_job(uid)
            await asyncio.sleep(0)
            return llm._with_background("x")

        return await asyncio.gather(asyncio.create_task(job("p-user-4")), asyncio.create_task(job("nobody")))

    mine, other = asyncio.run(two_jobs())
    assert "Secret detail" in mine and "Secret detail" not in other


def test_companion_prompt_can_be_replaced(client, monkeypatch):
    seen = fake_openai(monkeypatch)
    as_user(User("t-full-3", "me@x.y"))
    assert "prayer companion" in client.get("/api/options").json()["prompts"]["companion"]
    client.put("/api/profile", json={"companion_prompt": "You are a quiet listener. Ask one question."})
    client.post("/api/talk/session", json={"provider": "openai", "sdp": "o"})
    assert seen["instructions"].startswith("You are a quiet listener.") and "<background>" in seen["instructions"]
    client.put("/api/profile", json={"companion_prompt": ""})  # back to the default
    client.post("/api/talk/session", json={"provider": "openai", "sdp": "o"})
    assert seen["instructions"].startswith(talk.COMPANION[:40])
