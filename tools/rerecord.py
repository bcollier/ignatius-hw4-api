"""Re-record every day of a retreat with new voices, keeping the words exactly as
written (and the spoken guidance as tailored). Free voices cost nothing.

Run: .venv/bin/python tools/rerecord.py RETREAT_ID guide=VOICE reading=VOICE heart=VOICE deep=VOICE
Uses the storage and keys in .env, like the server. One day at a time.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pipeline  # noqa: E402


async def main(retreat_id: str, voices: dict) -> None:
    retreat = await pipeline.get(retreat_id)
    for n, state in sorted(retreat["days"].items(), key=lambda kv: int(kv[0])):
        p = state.get("params") or {}
        chosen = {**state.get("voices", {}), **voices}
        await pipeline.start_day_build(retreat, int(n), chosen, p.get("heart_prompt"), p.get("deep_prompt"),
                                       p.get("guide"), keep_scripts=True, model=p.get("model") or retreat["model"],
                                       search_provider=p.get("search_provider"))
        while state["status"] == "building":
            await asyncio.sleep(5)
        print(f"day {n}: {state['status']} {state.get('error') or ''}", flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], dict(arg.split("=", 1) for arg in sys.argv[2:])))
