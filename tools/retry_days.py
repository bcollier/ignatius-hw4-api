"""Finish a retreat's failed days by recording only the parts that failed.

Run: .venv/bin/python tools/retry_days.py RETREAT_ID
Uses the storage and keys in .env, like the server. One day at a time.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import pipeline  # noqa: E402


async def main(retreat_id: str) -> None:
    retreat = await pipeline.get(retreat_id)
    for n, state in sorted(retreat["days"].items(), key=lambda kv: int(kv[0])):
        if not pipeline.can_retry(state):
            print(f"day {n}: {state['status']}, nothing to retry" if state["status"] != "failed" else f"day {n}: needs rewriting")
            continue
        await pipeline.retry_failed(retreat, int(n))
        while state["status"] == "building":
            await asyncio.sleep(5)
        print(f"day {n}: {state['status']} {state.get('error') or ''}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
