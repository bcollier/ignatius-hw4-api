"""Text to speech with two tiers: free Microsoft Edge voices and premium ElevenLabs."""

import asyncio
import re
from pathlib import Path

import edge_tts
import httpx

from . import config

# Microsoft neural voices, used through the edge-tts package (no key, no cost).
FREE_VOICES = {
    "en-US-AndrewMultilingualNeural": "Andrew (warm, male)",
    "en-US-AvaMultilingualNeural": "Ava (caring, female)",
    "en-US-BrianMultilingualNeural": "Brian (easygoing, male)",
    "en-US-EmmaMultilingualNeural": "Emma (clear, female)",
    "en-US-ChristopherNeural": "Christopher (steady, male)",
    "en-US-AriaNeural": "Aria (confident, female)",
}

# ElevenLabs premade voices suited to prayer and teaching.
PREMIUM_VOICES = {
    "nPczCjzI2devNBz1zQrb": "Brian (deep, comforting, male)",
    "JBFqnCBsd6RMkjVDRZzb": "George (warm storyteller, British male)",
    "pqHfZKP75CvOlQylNhV4": "Bill (wise, mature, male)",
    "EXAVITQu4vr4xnSDxMaL": "Sarah (reassuring, female)",
    "Xb7hH8MSUJpSbSDYk0k2": "Alice (clear educator, British female)",
    "pFZP5JQG7iQjIQuC4Bku": "Lily (velvety, British female)",
}

ELEVENLABS_CHUNK = 2500
EDGE_CHUNK = 400
EDGE_PARALLEL = 6


class TTSError(RuntimeError):
    """Speech synthesis failed; the message is safe to show to the user."""


def tiers() -> dict:
    available = {"free": {"label": "Free (Microsoft voices)", "voices": FREE_VOICES, "max_chars": config.MAX_TRACK_CHARS}}
    if config.ELEVENLABS_API_KEY:
        available["premium"] = {
            "label": "Premium (ElevenLabs)",
            "voices": PREMIUM_VOICES,
            "max_chars": config.PREMIUM_MAX_TRACK_CHARS,
        }
    return available


def validate(tier: str, voice: str) -> None:
    available = tiers()
    if tier not in available:
        raise TTSError(f"Voice tier '{tier}' isn't available on this server.")
    if voice not in available[tier]["voices"]:
        raise TTSError(f"Unknown voice for the {tier} tier.")


def chunk_text(text: str, limit: int) -> list[str]:
    """Split on paragraph, then sentence, boundaries so no piece exceeds limit."""
    pieces: list[str] = []
    current = ""
    sentences = [s for para in text.split("\n") for s in re.split(r"(?<=[.!?])\s+", para.strip()) if s]
    for sentence in sentences:
        while len(sentence) > limit:  # a single enormous sentence
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        if current and len(current) + 1 + len(sentence) > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


async def synthesize(text: str, tier: str, voice: str, out_path: Path) -> None:
    validate(tier, voice)
    if tier == "free":
        await _edge(text, voice, out_path)
    else:
        await _elevenlabs(text, voice, out_path)


async def _edge_piece(text: str, voice: str) -> bytes:
    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice, rate="-5%").stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    return bytes(audio)


async def _edge(text: str, voice: str, out_path: Path) -> None:
    # The free service speaks at a bit faster than real time, so a long track is
    # split into pieces that are recorded in parallel and joined (same MP3 format).
    limit = asyncio.Semaphore(EDGE_PARALLEL)

    async def piece(part: str) -> bytes:
        async with limit:
            return await _edge_piece(part, voice)

    try:
        parts = await asyncio.gather(*(piece(p) for p in chunk_text(text, EDGE_CHUNK)))
    except Exception as exc:  # edge-tts raises several network and protocol errors
        raise TTSError("The free voice service didn't respond. Try again, or use the premium tier.") from exc
    out_path.write_bytes(b"".join(parts))


async def _elevenlabs(text: str, voice: str, out_path: Path) -> None:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
    headers = {"xi-api-key": config.ELEVENLABS_API_KEY}
    audio = bytearray()
    async with httpx.AsyncClient(timeout=120) as http:
        previous = ""
        for piece in chunk_text(text, ELEVENLABS_CHUNK):
            body = {"text": piece, "model_id": config.ELEVENLABS_MODEL, "previous_text": previous[-500:]}
            try:
                response = await http.post(url, headers=headers, params={"output_format": "mp3_44100_128"}, json=body)
            except httpx.HTTPError as exc:
                raise TTSError("Couldn't reach ElevenLabs.") from exc
            if response.status_code == 401:
                raise TTSError("ElevenLabs rejected the API key.")
            if response.status_code in (402, 429) or "quota" in response.text.lower():
                raise TTSError("ElevenLabs is out of credits or rate limited. Use the free tier or add credits.")
            if response.status_code >= 400:
                raise TTSError(f"ElevenLabs returned an error ({response.status_code}).")
            audio += response.content  # constant-bitrate MP3 pieces can be joined directly
            previous = piece
    out_path.write_bytes(bytes(audio))
