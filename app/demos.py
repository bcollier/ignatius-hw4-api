"""Demo retreats: ready-made retreats every signed-in person can open and pray, read
only. They are ordinary retreats owned by whoever built them, listed in
system/demos.json. What each person does with a demo (their start date, listening
progress, prayed days, journal) is kept in their own folder (demo_state.json) and
laid over the retreat when they read it, so demos stay the same for everyone.
"""

import copy
import json
import time
from datetime import date

from .storage import StorageError, store

REGISTRY = "system/demos.json"
_cache: dict = {}


async def registry() -> dict[str, dict]:
    """{retreat_id: {"label", "kind"}}, cached for a minute."""
    if _cache.get("at", 0) > time.time() - 60:
        return _cache["demos"]
    try:
        demos = json.loads(await store.get_file(REGISTRY))
    except (StorageError, ValueError):
        demos = {}
    _cache.update(at=time.time(), demos=demos)
    return demos


async def register(retreat_id: str, label: str, kind: str) -> None:
    demos = dict(await registry())
    demos[retreat_id] = {"label": label, "kind": kind}
    await store.put_file(REGISTRY, json.dumps(demos).encode(), "application/json")
    _cache.clear()


async def unregister(retreat_id: str) -> None:
    demos = dict(await registry())
    demos.pop(retreat_id, None)
    await store.put_file(REGISTRY, json.dumps(demos).encode(), "application/json")
    _cache.clear()


def _state_path(user_id: str) -> str:
    return f"{user_id}/demo_state.json"


async def load_state(user_id: str) -> dict:
    try:
        return json.loads(await store.get_file(_state_path(user_id)))
    except (StorageError, ValueError):
        return {}


async def save_state(user_id: str, state: dict) -> None:
    await store.put_file(_state_path(user_id), json.dumps(state).encode(), "application/json")


async def day_state(user_id: str, retreat_id: str, day: int) -> tuple[dict, dict]:
    """(everything, this day's own state) for writing; the caller saves with save_state."""
    state = await load_state(user_id)
    mine = state.setdefault(retreat_id, {"start_date": date.today().isoformat(), "days": {}})
    return state, mine["days"].setdefault(str(day), {})


def personal(retreat: dict, mine: dict | None, meta: dict) -> dict:
    """The demo as this person sees it: their start date and their progress."""
    view = copy.deepcopy(retreat)
    mine = mine or {}
    view["start_date"] = mine.get("start_date") or date.today().isoformat()
    for n, d in view.get("days", {}).items():
        own = (mine.get("days") or {}).get(n, {})
        for key in ("prayed_at", "journal", "listening"):
            d[key] = own.get(key)
    view["demo"] = meta
    view["read_only"] = True
    view.pop("costs", None)
    for d in view.get("days", {}).values():
        d.pop("cost", None)
    return view
