"""Build a demo retreat into the configured store (Supabase in production) from the
demo package, owned by the given user, and register it as a demo everyone can open.

  .venv/bin/python tools/build_demo.py free    OWNER_USER_ID
  .venv/bin/python tools/build_demo.py premium OWNER_USER_ID

free:    planned and written by Muse Glimmer (Jetstream), researched with every free search service combined, Microsoft voices.
premium: planned and written by Claude Fable 5.1 with web search, recorded with ElevenLabs
         (Sarah as guide, George reading, Brian for the reflection, Alice for the deep dive).
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from app import demos, llm_log, pipeline, pricing, prompts  # noqa: E402
from app.extract import extract  # noqa: E402

PDF = ROOT / "samples" / "demo" / "come-and-see.pdf"
FREE_VOICES = {"guide": "en-US-AvaMultilingualNeural", "reading": "en-US-AndrewMultilingualNeural",
               "heart": "en-US-AndrewMultilingualNeural", "deep": "en-US-ChristopherNeural"}
PREMIUM_VOICES = {"guide": "EXAVITQu4vr4xnSDxMaL",  # Sarah
                  "reading": "JBFqnCBsd6RMkjVDRZzb",  # George
                  "heart": "nPczCjzI2devNBz1zQrb",  # Brian
                  "deep": "Xb7hH8MSUJpSbSDYk0k2"}  # Alice
KINDS = {
    "free": {"model": "jetstream/muse-glimmer", "search": "all",
             "label": "Example retreat · free models and voices"},
    "premium": {"model": "anthropic/claude-fable-5.1", "search": None,
                "label": "Example retreat · Claude Fable and ElevenLabs voices"},
}


def opts(kind: str, voices: dict) -> dict:
    k = KINDS[kind]
    o = {"voices": voices, "heart_prompt": prompts.HEART_PRESETS["companion"], "deep_prompt": prompts.DEEP_INSTRUCTIONS,
         "guide": dict(prompts.GUIDE_DEFAULTS), "write_model": k["model"], "search_provider": k["search"], "tailor_guide": True}
    return o


async def wait(retreat: dict, label: str) -> None:
    while pipeline._busy(retreat):
        p = retreat.get("progress") or {}
        print(f"  {label}: {retreat['status']} {p.get('done', 0)}/{p.get('total', '?')}", flush=True)
        await asyncio.sleep(30)


async def main(kind: str, owner: str) -> None:
    k = KINDS[kind]
    llm_log.tag(email=f"demo build ({kind})")
    if kind == "premium":
        balance = (await pricing.elevenlabs_balance()) or {}
        print(f"ElevenLabs: {balance.get('remaining')} characters left", flush=True)
    source = extract(PDF.name, PDF.read_bytes())
    voices = PREMIUM_VOICES if kind == "premium" else FREE_VOICES
    retreat = await pipeline.create_retreat(owner, PDF.name, source, prompts.PLAN_INSTRUCTIONS, k["model"], email="demo",
                                            build_options=opts(kind, voices), start_date=time.strftime("%Y-%m-%d"))
    print(f"{kind} demo: {retreat['id']}", flush=True)
    await wait(retreat, kind)
    await demos.register(retreat["id"], k["label"], kind)
    print(f"done: {retreat['id']} {retreat['status']} failed={retreat['progress']['failed']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
