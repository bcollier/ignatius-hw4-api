"""Rewrite and re-record some parts of every day of a retreat with the current default
prompts, keeping the others exactly as they are.

Run: .venv/bin/python tools/rewrite_parts.py RETREAT_ID heart guide
     (parts: heart, deep, guide; the ones not named are kept, words and recording)
Uses the storage and keys in .env, like the server. One day at a time.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pipeline, prompts  # noqa: E402

PARTS = {"heart", "deep", "guide"}


async def main(retreat_id: str, rewrite: set[str]) -> None:
    retreat = await pipeline.get(retreat_id)
    for n, state in sorted(retreat["days"].items(), key=lambda kv: int(kv[0])):
        params = state.get("params") or {}
        opts = {"voices": state["voices"], "heart_prompt": prompts.HEART_PRESETS["companion"],
                "deep_prompt": prompts.DEEP_INSTRUCTIONS, "guide": dict(prompts.GUIDE_DEFAULTS),
                "write_model": params.get("model") or retreat["model"], "search_provider": params.get("search_provider")}
        kept, meter = await pipeline._prepare_day(retreat, int(n), opts, keep_scripts=True)
        for part in rewrite:
            kept.pop(part, None)  # written again; the rest is reused as it was
        pipeline.spawn(retreat, pipeline._build_day(retreat, int(n), opts["heart_prompt"], opts["deep_prompt"],
                                                    opts["guide"], kept, meter, opts["search_provider"]))
        while state["status"] == "building":
            await asyncio.sleep(5)
        print(f"day {n}: {state['status']} {state.get('error') or ''}", flush=True)


if __name__ == "__main__":
    wanted = set(sys.argv[2:])
    assert wanted and wanted <= PARTS, f"name parts from {sorted(PARTS)}"
    asyncio.run(main(sys.argv[1], wanted))
