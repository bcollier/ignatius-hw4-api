"""Text to speech with two tiers: free Microsoft Edge voices and premium ElevenLabs."""

import asyncio
import base64
import json
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
    "en-GB-RyanNeural": "Ryan (calm, British male)",
    "en-GB-SoniaNeural": "Sonia (gentle, British female)",
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
# Shared by every recording in the process, so a day with many sections doesn't
# open dozens of connections to the free service at once.
_edge_slots = asyncio.Semaphore(EDGE_PARALLEL)
# ElevenLabs plans cap simultaneous requests (Starter: 3); a day's many short guidance
# clips would otherwise all start at once and be refused with 429.
ELEVENLABS_PARALLEL = 2
ELEVENLABS_ATTEMPTS = 5
_eleven_slots = asyncio.Semaphore(ELEVENLABS_PARALLEL)


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


def tier_of(voice: str) -> str:
    """Which tier a voice belongs to. Voice ids are unique across tiers."""
    for tier, info in tiers().items():
        if voice in info["voices"]:
            return tier
    raise TTSError(f"Unknown or unavailable voice: {voice}")


def max_chars(voice: str) -> int:
    return tiers()[tier_of(voice)]["max_chars"]


# Both services return constant-bitrate MP3, so length follows from file size.
BYTES_PER_SECOND = {"free": 48_000 / 8, "premium": 128_000 / 8}


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


def words_path(out_path: Path) -> Path:
    """Where synthesize leaves the word timings for a recording, if the service gave them."""
    return out_path.with_suffix(".words.json")


def align(text: str, spoken: list[tuple[float, str]]) -> list[list]:
    """[[seconds, character index in text], ...] for each spoken word found in text,
    in order, so the page can highlight the word being read."""
    out, cursor = [], 0
    for t, word in spoken:
        word = word.strip()
        if not word:
            continue
        i = text.find(word, cursor)
        if i < 0 or i - cursor > 80:  # not where we expected it: skip rather than jump ahead
            continue
        out.append([round(t, 2), i])
        cursor = i + len(word)
    return out


def words_from_alignment(alignment: dict, offset: float) -> list[tuple[float, str]]:
    """ElevenLabs character timings to (start time, word) pairs."""
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    words, current, start = [], "", 0.0
    for ch, t in zip(chars, starts):
        if ch.isspace():
            if current:
                words.append((start, current))
            current = ""
        else:
            if not current:
                start = offset + t
            current += ch
    if current:
        words.append((start, current))
    return words


async def synthesize(text: str, voice: str, out_path: Path) -> float:
    """Record text with the voice; returns the length in seconds. Word timings, when
    the service gives them, are written next to the recording (words_path)."""
    tier = tier_of(voice)
    if tier == "free":
        await _edge(text, voice, out_path)
    else:
        await _elevenlabs(text, voice, out_path)
    return round(out_path.stat().st_size / BYTES_PER_SECOND[tier], 1)


EDGE_ATTEMPTS = 4


async def _edge_piece(text: str, voice: str) -> tuple[bytes, list[tuple[float, str]]]:
    """One piece from the free service, retried with a growing pause: the service
    sometimes drops a connection or returns nothing, especially under load."""
    for attempt in range(EDGE_ATTEMPTS):
        try:
            audio, words = bytearray(), []
            async for chunk in edge_tts.Communicate(text, voice, rate="-5%", boundary="WordBoundary").stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
                elif chunk["type"] == "WordBoundary":
                    words.append((chunk["offset"] / 10_000_000, chunk["text"]))  # offsets are in 100 ns units
            if audio:
                return bytes(audio), words
            raise RuntimeError("no audio returned")
        except Exception:
            if attempt == EDGE_ATTEMPTS - 1:
                raise
            await asyncio.sleep(1.5 * 2**attempt)  # 1.5 s, 3 s, 6 s
    raise RuntimeError("unreachable")


async def _edge(text: str, voice: str, out_path: Path) -> None:
    # The free service speaks at a bit faster than real time, so a long track is
    # split into pieces that are recorded in parallel and joined (same MP3 format).
    async def piece(part: str) -> bytes:
        async with _edge_slots:
            return await _edge_piece(part, voice)

    try:
        parts = await asyncio.gather(*(piece(p) for p in chunk_text(text, EDGE_CHUNK)))
    except Exception as exc:  # edge-tts raises several network and protocol errors
        raise TTSError("The free voice service didn't respond. Try again, or use the premium tier.") from exc
    out_path.write_bytes(b"".join(audio for audio, _ in parts))
    spoken, offset = [], 0.0
    for audio, words in parts:
        spoken += [(offset + t, w) for t, w in words]
        offset += len(audio) / BYTES_PER_SECOND["free"]
    _write_words(text, spoken, out_path)


def _write_words(text: str, spoken: list[tuple[float, str]], out_path: Path) -> None:
    words = align(text, spoken)
    if words:
        words_path(out_path).write_text(json.dumps(words))


ELEVENLABS_FORMAT = "mp3_44100_128"
PREVIOUS_TEXT_CHARS = 500  # the text before each piece, so the voice carries on in the same tone
ELEVENLABS_TIMEOUT = 120


async def _elevenlabs(text: str, voice: str, out_path: Path) -> None:
    # The with-timestamps endpoint costs the same and also gives each character's time.
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"
    audio = bytearray()
    spoken: list[tuple[float, str]] = []
    async with httpx.AsyncClient(timeout=ELEVENLABS_TIMEOUT) as http:
        previous = ""
        for piece in chunk_text(text, ELEVENLABS_CHUNK):
            body = {"text": piece, "model_id": config.ELEVENLABS_MODEL, "previous_text": previous[-PREVIOUS_TEXT_CHARS:]}
            response = await _eleven_post(http, url, body)
            _check_eleven_response(response)
            clip, alignment = _eleven_audio(response)
            spoken += words_from_alignment(alignment, len(audio) / BYTES_PER_SECOND["premium"])
            audio += clip  # constant-bitrate MP3 pieces can be joined directly
            previous = piece
    out_path.write_bytes(bytes(audio))
    _write_words(text, spoken, out_path)


def _backoff(attempt: int) -> float:
    return 2 * 2**attempt  # 2, 4, 8, 16 seconds


async def _eleven_post(http: httpx.AsyncClient, url: str, body: dict) -> httpx.Response:
    """One piece, retried when ElevenLabs can't be reached or has too many requests at once."""
    headers = {"xi-api-key": config.ELEVENLABS_API_KEY}
    for attempt in range(ELEVENLABS_ATTEMPTS):
        last_try = attempt == ELEVENLABS_ATTEMPTS - 1
        try:
            async with _eleven_slots:
                response = await http.post(url, headers=headers, params={"output_format": ELEVENLABS_FORMAT}, json=body)
        except httpx.HTTPError as exc:
            if last_try:
                raise TTSError("Couldn't reach ElevenLabs.") from exc
            await asyncio.sleep(_backoff(attempt))
            continue
        if _is_busy(response) and not last_try:
            await asyncio.sleep(_backoff(attempt))  # too many at once: wait our turn
            continue
        return response
    raise TTSError("Couldn't reach ElevenLabs.")  # not reached: the last try returns or raises


def _is_busy(response: httpx.Response) -> bool:
    """429 without "quota" means too many requests at once (an error body, not audio)."""
    return response.status_code == 429 and "quota" not in response.text.lower()


def _check_eleven_response(response: httpx.Response) -> None:
    status = response.status_code
    if status == 401:
        raise TTSError("ElevenLabs rejected the API key.")
    if _is_busy(response):
        raise TTSError("ElevenLabs is busy (too many requests at once). Try again in a minute.")
    if status == 402 or (status >= 400 and "quota" in response.text.lower()):
        raise TTSError("ElevenLabs is out of credits. Use the free tier or add credits.")
    if status >= 400:
        raise TTSError(f"ElevenLabs returned an error ({status}).")


def _eleven_audio(response: httpx.Response) -> tuple[bytes, dict]:
    """The MP3 bytes and the character timings from a with-timestamps reply."""
    try:
        data = response.json()
        return base64.b64decode(data["audio_base64"]), data.get("alignment") or {}
    except (ValueError, KeyError) as exc:
        raise TTSError("ElevenLabs returned an unexpected response.") from exc
