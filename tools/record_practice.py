"""Record the prayer practice's narration (practice.json from make_practice.py) in two
voices, Standard (Microsoft, free) and Deluxe (ElevenLabs), once for everyone, and add
each clip's file and length to practice.json. Prints what ElevenLabs charged.

Run: .venv/bin/python tools/record_practice.py PRACTICE_DIR
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pricing, tts  # noqa: E402

VOICES = {
    "standard": ("en-US-AvaMultilingualNeural", "Ava, Microsoft (free)"),
    "deluxe": ("EXAVITQu4vr4xnSDxMaL", "Sarah, ElevenLabs"),
}
# A session can have its own voices (the Examen is led by a British man).
SESSION_VOICES = {
    "examen": {
        "standard": ("en-GB-RyanNeural", "Ryan, Microsoft (free)"),
        "deluxe": ("JBFqnCBsd6RMkjVDRZzb", "George, ElevenLabs"),
    },
}
# Sessions recorded only in the free voice (no ElevenLabs credits).
FREE_ONLY = {"my-dossier", "birth"}
# The sleep prayers: only a warm Irish voice (ElevenLabs), levelled to one loudness.
SLEEP_VOICE = ("1OYA2kgM85gF2eGN8HEp", "Colleen, ElevenLabs (Irish)")
for _sid in ("sleep-10", "sleep-30", "sleep-45"):
    SESSION_VOICES[_sid] = {"deluxe": SLEEP_VOICE}
DELUXE_ONLY = {"sleep-10", "sleep-30", "sleep-45"}


async def _balance() -> dict:
    pricing._eleven = None  # the app caches it for five minutes; read it fresh
    return (await pricing.elevenlabs_balance()) or {}


async def main(folder: Path) -> None:
    path = folder / "practice.json"
    practice = json.loads(path.read_text())
    before = await _balance()
    practice["voices"] = {k: label for k, (_, label) in VOICES.items()}
    for tier in VOICES:
        (folder / tier).mkdir(exist_ok=True)
        for session in practice["sessions"]:
            voices = SESSION_VOICES.get(session["id"], VOICES)
            if session["id"] in FREE_ONLY:
                voices = {"standard": voices["standard"]}
                if tier not in voices:
                    session["voices"] = {"standard": voices["standard"][1]}
                    continue
            if session["id"] in DELUXE_ONLY:
                if tier != "deluxe":
                    session["voices"] = {"deluxe": SLEEP_VOICE[1]}
                    continue
            voice = voices[tier][0]
            session["voices"] = {k: label for k, (_, label) in voices.items()}
            for n, seg in enumerate(session["segments"], start=1):
                if seg["kind"] != "speak":
                    continue
                name = f"{tier}/{session['id']}-{n:02d}.mp3"
                if seg.get("audio", {}).get(tier) and (folder / name).exists():
                    continue  # already recorded
                seconds = await tts.synthesize(seg["text"], voice, folder / name)
                seg.setdefault("audio", {})[tier] = {"file": f"practice/{name}", "seconds": seconds}
                print(tier, name, seconds, flush=True)
    after = await _balance()
    chars = sum(len(g["text"]) for s in practice["sessions"] for g in s["segments"] if g["kind"] == "speak")
    used = (before.get("remaining") or 0) - (after.get("remaining") or 0)
    practice.setdefault("recordings", []).append({"characters": chars, "elevenlabs_credits_used": used})
    path.write_text(json.dumps(practice, indent=1, ensure_ascii=False))
    print(f"{chars:,} characters; ElevenLabs credits used: {used:,} ({before.get('remaining')} → {after.get('remaining')})")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
