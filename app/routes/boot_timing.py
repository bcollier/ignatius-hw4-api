"""How long people wait for the server when they open the app.

The server sleeps after a quiet spell (Render's free tier) and the first visitor waits
while it starts. Each page load measures its wait for /api/health and reports it here,
with whether the server had just started (its uptime is shorter than the wait: a cold
start). A GitHub Actions job adds one check a day (source "probe"), so there is a
measure even on days nobody visits. Kept per day in the storage bucket; no sign-in to
report, so reports are limited per address, and nothing about the person is stored."""

import json
import logging
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from ..auth import User, current_user
from ..storage import StorageError, store
from .client_errors import _allowed
from .handoff import client_address

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
MAX_PER_DAY = 2000
MAX_WAIT_MS = 10 * 60 * 1000
_summary: dict = {}  # {days: (made_at, result)}, for a few minutes


class BootTiming(BaseModel):
    wait_ms: int = Field(ge=0, le=MAX_WAIT_MS)
    cold: bool = False
    up_seconds: float | None = Field(None, ge=0)
    source: Literal["app", "probe"] = "app"
    version: str = Field("", max_length=20)


def _path(day: str) -> str:
    return f"_boot_timing/{day}.json"


@router.post("/boot-timing", status_code=204)
async def report(body: BootTiming, request: Request):
    if not _allowed(client_address(request)):
        return
    now = datetime.now(timezone.utc)
    path = _path(f"{now:%Y-%m-%d}")
    try:
        entries = json.loads(await store.get_file(path))
    except (StorageError, ValueError):
        entries = []
    if len(entries) >= MAX_PER_DAY:
        return
    entries.append({"at": now.isoformat(timespec="seconds"), **body.model_dump()})
    try:
        await store.put_file(path, json.dumps(entries).encode(), "application/json")
    except StorageError:
        log.exception("couldn't keep a boot timing")


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p * (len(xs) - 1)))]


def day_summary(day: str, entries: list[dict]) -> dict:
    cold = [e["wait_ms"] for e in entries if e.get("cold")]
    warm = [e["wait_ms"] for e in entries if not e.get("cold")]
    probes = [e["wait_ms"] for e in entries if e.get("source") == "probe"]
    return {"day": day, "n": len(entries), "cold_n": len(cold),
            "cold_median_ms": statistics.median(cold) if cold else None, "cold_p90_ms": _pct(cold, 0.9),
            "cold_max_ms": max(cold) if cold else None,
            "warm_median_ms": statistics.median(warm) if warm else None,
            "probe_ms": probes[-1] if probes else None}


@router.get("/boot-timing")
async def summary(days: int = Query(30, ge=1, le=120), _user: User = Depends(current_user)):
    """One row per day, oldest first, and the typical cold start over the whole span."""
    made = _summary.get(days)
    if made and time.time() - made[0] < 300:
        return made[1]
    today = datetime.now(timezone.utc).date()
    rows, all_cold = [], []
    for back in range(days - 1, -1, -1):
        day = f"{today - timedelta(days=back):%Y-%m-%d}"
        try:
            entries = json.loads(await store.get_file(_path(day)))
        except (StorageError, ValueError):
            continue
        rows.append(day_summary(day, entries))
        all_cold += [e["wait_ms"] for e in entries if e.get("cold")]
    result = {"days": rows, "cold_n": len(all_cold),
              "typical_cold_ms": statistics.median(all_cold) if all_cold else None,
              "cold_p90_ms": _pct(all_cold, 0.9)}
    _summary[days] = (time.time(), result)
    return result
