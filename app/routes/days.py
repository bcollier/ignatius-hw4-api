"""One day of a retreat: marking it prayed, listening progress, and rebuilding it."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import llm_log, pipeline, quotas, tts
from ..access import day_state, my_retreat, now_iso, readable_retreat, save_retreat, view_of
from ..auth import User, current_user
from ..checks import BuildRequest, resolve_build

router = APIRouter(prefix="/api/retreats/{retreat_id}/days/{day}")

MAX_WORD_CHARS = 100
MAX_NOTE_CHARS = 2000
# Listening progress: how much of what the browser reports is kept.
MAX_PART_NAME_CHARS = 40
MAX_PARTS_PER_REPORT = 50
MAX_LAST_PART_CHARS = 60


class PrayedRequest(BaseModel):
    prayed: bool = True
    word: str | None = None
    note: str | None = None


class ProgressRequest(BaseModel):
    step: int = 0
    part: str = ""
    seconds: float = 0
    parts_played: list[str] = []
    finished: bool = False
    began_at: str | None = Field(None, max_length=40)  # when this sitting started, for the calendar


MAX_PRAYED_LOG = 60


SAME_SITTING = 20 * 60  # a second "prayed" within 20 minutes is the same sitting


def remember_prayed(state: dict, began_at: str | None = None) -> None:
    """Keep when a day was prayed even if it's later unmarked: "Last prayed …" on the day,
    and the calendar feed's events, each at the time it was finished (from when it began,
    when the player said so)."""
    now = now_iso()
    state["last_prayed_at"] = now
    log = [e if isinstance(e, dict) else {"at": e} for e in state.get("prayed_log") or []]  # older entries: a time only
    began = _when(began_at)
    entry = {"at": now, **({"from": began} if began and began < now else {})}
    if log and (datetime.fromisoformat(now) - datetime.fromisoformat(log[-1]["at"])).total_seconds() < SAME_SITTING:
        log[-1] = {**log[-1], **entry, "from": log[-1].get("from") or entry.get("from")}
    else:
        log.append(entry)
    state["prayed_log"] = log[-MAX_PRAYED_LOG:]


def _when(text: str | None) -> str | None:
    """A time from the browser, as UTC ISO, or None if it isn't one (or is far off)."""
    try:
        t = datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        return None
    t = t.astimezone(timezone.utc)
    ago = (datetime.now(timezone.utc) - t).total_seconds()
    return t.isoformat() if 0 <= ago < 6 * 3600 else None


@router.post("/prayed")
async def mark_prayed(day: int, body: PrayedRequest, retreat: dict = Depends(readable_retreat)):
    """Mark a day prayed (or not), and keep the word that stayed and a short note."""
    state = day_state(retreat, day)
    word, note = (body.word or "").strip(), (body.note or "").strip()
    if len(word) > MAX_WORD_CHARS:
        raise HTTPException(400, f"The word or phrase can be up to {MAX_WORD_CHARS} characters.")
    if len(note) > MAX_NOTE_CHARS:
        raise HTTPException(400, f"The note can be up to {MAX_NOTE_CHARS:,} characters.")
    state["prayed_at"] = (state.get("prayed_at") or now_iso()) if body.prayed else None
    if body.prayed:
        remember_prayed(state)
    if word or note:
        state["journal"] = {"word": word, "note": note, "at": now_iso()}
    elif body.word is not None or body.note is not None:
        state["journal"] = None  # both cleared
    await save_retreat(retreat)
    return await view_of(retreat)


@router.post("/progress")
async def listening_progress(day: int, body: ProgressRequest, retreat: dict = Depends(readable_retreat)):
    """What has been played of a day, so a missed or interrupted day is known later and
    can be continued on any device. Finishing the prayer marks the day prayed."""
    state = day_state(retreat, day)
    now = now_iso()
    listening = state.get("listening") or {"parts_played": [], "started_at": now}
    reported = [p[:MAX_PART_NAME_CHARS] for p in body.parts_played][:MAX_PARTS_PER_REPORT]
    listening.update(parts_played=list(dict.fromkeys(listening["parts_played"] + reported)),
                     last_step=max(0, body.step), last_part=body.part[:MAX_LAST_PART_CHARS],
                     seconds_in_part=max(0.0, body.seconds), updated_at=now)
    if body.finished:
        listening["finished_at"] = now
        state["prayed_at"] = state.get("prayed_at") or now
        remember_prayed(state, body.began_at)
    state["listening"] = listening
    await save_retreat(retreat)
    return {"listening": listening, "prayed_at": state.get("prayed_at")}


@router.post("/build", status_code=202)
async def build_day(
    day: int, body: BuildRequest, retreat: dict = Depends(my_retreat), user: User = Depends(current_user)
):
    """Rebuild one day: Rewrite, Re-record (keep_scripts) or Try again."""
    _check_can_rebuild(retreat, day)
    opts = resolve_build(body, user)
    await quotas.admit(user, count=False)
    llm_log.tag(email=user.log_email)  # inherited by the build job
    try:
        await pipeline.start_day_build(retreat, day, opts["voices"], opts["heart_prompt"], opts["deep_prompt"],
                                       opts["guide"], body.keep_scripts, opts["write_model"], opts["search_provider"])
    except tts.TTSError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await pipeline.public_view(retreat)


def _check_can_rebuild(retreat: dict, day: int) -> None:
    if retreat["status"] == "building":
        raise HTTPException(409, "This retreat is still being made. Wait for it to finish.")
    if retreat["status"] != "ready":
        raise HTTPException(409, "The retreat plan isn't ready yet.")
    state = retreat["days"].get(str(day))
    if state is None:
        raise HTTPException(404, f"This retreat has no day {day}.")
    if state["status"] in ("building", "queued"):
        raise HTTPException(409, f"Day {day} is already being made.")


@router.post("/retry", status_code=202)
async def retry_day(day: int, retreat: dict = Depends(my_retreat), user: User = Depends(current_user)):
    """Try again after a failure: record only the parts that failed, from their saved
    scripts, with the day's own options. Nothing is written again."""
    state = day_state(retreat, day)
    if not pipeline.can_retry(state):
        raise HTTPException(409, f"Day {day} can't be finished from its scripts; rewrite it instead.")
    await quotas.admit(user, count=False)
    llm_log.tag(email=user.log_email)
    await pipeline.retry_failed(retreat, day)
    return await pipeline.public_view(retreat)
