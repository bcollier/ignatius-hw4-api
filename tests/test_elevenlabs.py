"""ElevenLabs recording against a fake server: pieces joined, word timings kept,
too-many-at-once retried, and each kind of failure explained."""

import asyncio
import base64

import httpx
import pytest

from app import config, tts


def fake_server(monkeypatch, replies):
    """Answer each request with the next reply: a status code, or "ok" for audio."""
    real = httpx.AsyncClient
    calls = []

    def handler(request):
        calls.append(request)
        reply = replies[min(len(calls) - 1, len(replies) - 1)]
        if reply == "ok":
            chars = list("Hi there")
            return httpx.Response(200, json={"audio_base64": base64.b64encode(b"MP3" * 1000).decode(),
                                             "alignment": {"characters": chars,
                                                           "character_start_times_seconds": [i / 10 for i in range(len(chars))]}})
        return httpx.Response(reply, text="quota_exceeded" if reply == 402 else "slow down")

    async def no_sleep(_):
        return None

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(tts.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "k")
    return calls


def test_busy_is_retried_then_recorded(monkeypatch, tmp_path):
    calls = fake_server(monkeypatch, [429, 429, "ok"])
    out = tmp_path / "clip.mp3"
    asyncio.run(tts._elevenlabs("Hi there", "voice", out))
    assert len(calls) == 3 and out.read_bytes() == b"MP3" * 1000
    assert tts.words_path(out).exists()


@pytest.mark.parametrize("status, words", [(401, "rejected the API key"), (402, "out of credits"),
                                           (429, "busy"), (500, "error (500)")])
def test_failures_are_explained(monkeypatch, tmp_path, status, words):
    fake_server(monkeypatch, [status])
    with pytest.raises(tts.TTSError, match=words.replace("(", r"\(").replace(")", r"\)")):
        asyncio.run(tts._elevenlabs("Hi there", "voice", tmp_path / "clip.mp3"))
