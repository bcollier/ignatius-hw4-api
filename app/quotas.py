"""How much work one person, and the whole server, may start.

Making a retreat, rebuilding a day and writing an Examen all spend model, search and
voice credits and hold a background job. Each is admitted here first: a person may run
only a few jobs at once and start only so many a day (free accounts, guests included,
fewer), and the server refuses new work when it's already carrying MAX_ACTIVE jobs.
Refusals say plainly what to do.
"""

import asyncio
import json
from datetime import date

from fastapi import HTTPException

from . import pipeline
from .storage import StorageError, store

LIMITS = {"free": {"at_once": 1, "per_day": 5}, "full": {"at_once": 3, "per_day": 50}}
MAX_ACTIVE = 12  # jobs on the whole server at once
_locks: dict[str, asyncio.Lock] = {}


def _path(user_id: str) -> str:
    return f"{user_id}/usage/jobs.json"


async def jobs_today(user_id: str) -> int:
    try:
        return int(json.loads(await store.get_file(_path(user_id))).get(date.today().isoformat(), 0))
    except (StorageError, ValueError):
        return 0


async def admit(user, count: bool = True) -> None:
    """Allow one more job for this person, or raise 429 (theirs) or 503 (the server's).
    With `count`, it's added to today's total (a new retreat, an Examen); a rebuild of a
    day in a retreat they already made passes count=False and is only held to at_once."""
    limits = LIMITS["full" if user.full else "free"]
    async with _locks.setdefault(user.id, asyncio.Lock()):
        if len(pipeline.active) >= MAX_ACTIVE:
            raise HTTPException(503, "The server is busy making other retreats. Please try again in a few minutes.")
        mine = sum(1 for r in pipeline.active.values() if r.get("user_id") == user.id)
        if mine >= limits["at_once"]:
            raise HTTPException(429, f"You already have {mine} {'retreat' if mine == 1 else 'retreats'} being made. "
                                     "Wait for it to finish, then start the next.")
        if count:
            done = await jobs_today(user.id)
            if done >= limits["per_day"]:
                raise HTTPException(429, f"That's today's {limits['per_day']} new retreats. Please come back tomorrow.")
            await store.put_file(_path(user.id), json.dumps({date.today().isoformat(): done + 1}).encode(),
                                 "application/json")
