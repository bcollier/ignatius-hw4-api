"""Who may see and change a retreat. People see their own retreats; everyone signed
in can also read the example retreats, with their own progress laid over them."""

from datetime import datetime, timezone

from fastapi import Depends, HTTPException

from . import demos, pipeline
from .auth import User, current_user

# What a person may change in an example retreat: their own progress, kept apart.
PERSONAL_DAY_FIELDS = ("prayed_at", "journal", "listening")


async def my_retreat(retreat_id: str, user: User = Depends(current_user)) -> dict:
    """The person's own retreat (for building, rebuilding and deleting)."""
    retreat = await pipeline.get(retreat_id)
    # Someone else's retreat gets the same answer as a missing one.
    if not retreat or retreat["user_id"] != user.id:
        raise HTTPException(404, "Retreat not found.")
    return retreat


async def readable_retreat(retreat_id: str, user: User = Depends(current_user)) -> dict:
    """The person's own retreat, or an example retreat seen with their own progress laid
    over it (marked read_only; save changes with save_retreat). Examples are read only
    on the site even for the account that built them; tools/ can still rebuild them."""
    retreat = await pipeline.get(retreat_id)
    meta = (await demos.registry()).get(retreat_id)
    if retreat and retreat["user_id"] == user.id and not meta:
        return retreat
    if not retreat or not meta:
        raise HTTPException(404, "Retreat not found.")
    mine = (await demos.load_state(user.id)).get(retreat_id)
    view = demos.personal(retreat, mine, meta)
    view["_viewer"] = user.id
    return view


async def save_retreat(retreat: dict) -> None:
    """Owners save the retreat; for an example only the person's own progress is kept."""
    if not retreat.get("read_only"):
        await pipeline.save(retreat)
        return
    everything = await demos.load_state(retreat["_viewer"])
    everything[retreat["id"]] = {
        "start_date": retreat.get("start_date"),
        "days": {n: {k: d.get(k) for k in PERSONAL_DAY_FIELDS} for n, d in retreat["days"].items()},
    }
    await demos.save_state(retreat["_viewer"], everything)


async def view_of(retreat: dict) -> dict:
    """The retreat as the API returns it (the internal viewer marker removed)."""
    view = await pipeline.public_view(retreat)
    view.pop("_viewer", None)
    return view


def day_state(retreat: dict, day: int) -> dict:
    state = retreat["days"].get(str(day))
    if state is None:
        raise HTTPException(404, f"This retreat has no day {day}.")
    return state


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
