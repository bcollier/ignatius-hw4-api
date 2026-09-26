"""Build an example retreat into the configured store (Supabase in production),
owned by the given user, and register it as an example everyone can open, replacing
the previous example of the same kind once the new one is ready.

  .venv/bin/python tools/build_demo.py free    OWNER_USER_ID
  .venv/bin/python tools/build_demo.py premium OWNER_USER_ID

free:    "Blessed" (samples/examples/blessed.pdf), planned and written by Muse Glimmer (Jetstream),
         researched with every free search service combined, Microsoft voices (Ava, Andrew, Emma, Christopher).
premium: "Come and See" (samples/demo/come-and-see.pdf), planned and written by Claude Fable 5.1 with
         the free research as a head start and its own web search, recorded with ElevenLabs
         (Sarah as guide, George reading, Brian for the reflection, Alice for the deep dive).
"""

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from app import demos, llm_log, pipeline, pricing, prompts  # noqa: E402
from app.extract import extract  # noqa: E402

FREE_VOICES = {"guide": "en-US-AvaMultilingualNeural", "reading": "en-US-AndrewMultilingualNeural",
               "heart": "en-US-EmmaMultilingualNeural", "deep": "en-US-ChristopherNeural"}
PREMIUM_VOICES = {"guide": "EXAVITQu4vr4xnSDxMaL",  # Sarah
                  "reading": "JBFqnCBsd6RMkjVDRZzb",  # George
                  "heart": "nPczCjzI2devNBz1zQrb",  # Brian
                  "deep": "Xb7hH8MSUJpSbSDYk0k2"}  # Alice
KINDS = {
    "free": {"model": "jetstream/muse-glimmer", "search": "all", "source": ROOT / "samples" / "examples" / "blessed.pdf",
             "label": "Example retreat · free models and voices"},
    "premium": {"model": "anthropic/claude-fable-5.1", "search": "all", "source": ROOT / "samples" / "demo" / "come-and-see.pdf",
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


async def retry_failed_days(retreat: dict, kind: str) -> None:
    """Record again any parts that failed (a voice service hiccup), from their scripts."""
    for n, state in sorted(retreat["days"].items(), key=lambda kv: int(kv[0])):
        if pipeline.can_retry(state):
            await pipeline.retry_failed(retreat, int(n))
            while state["status"] == "building":
                await asyncio.sleep(5)
            print(f"  {kind}: retried day {n}: {state['status']}", flush=True)


async def main(kind: str, owner: str) -> None:
    k = KINDS[kind]
    llm_log.tag(email=f"demo build ({kind})")
    if kind == "premium":
        balance = (await pricing.elevenlabs_balance()) or {}
        print(f"ElevenLabs: {balance.get('remaining')} characters left", flush=True)
    pdf = k["source"]
    source = extract(pdf.name, pdf.read_bytes())
    voices = PREMIUM_VOICES if kind == "premium" else FREE_VOICES
    retreat = await pipeline.create_retreat(owner, pdf.name, source, prompts.PLAN_INSTRUCTIONS, k["model"], email="demo",
                                            build_options=opts(kind, voices), start_date=time.strftime("%Y-%m-%d"))
    print(f"{kind} demo: {retreat['id']}", flush=True)
    await wait(retreat, kind)
    await retry_failed_days(retreat, kind)
    previous = [rid for rid, meta in (await demos.registry()).items() if meta.get("kind") == kind]
    await demos.register(retreat["id"], k["label"], kind)
    for rid in previous:  # the new example replaces the old one
        await demos.unregister(rid)
        print(f"replaced example {rid}", flush=True)
    print(f"done: {retreat['id']} {retreat['status']} failed={retreat['progress']['failed']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
