"""Talk it over: starting and ending a live conversation, and its remembered history."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import profile, talk
from ..access import readable_retreat
from ..auth import User, current_user

router = APIRouter(prefix="/api/talk")


class TalkRequest(BaseModel):
    provider: str | None = None
    voice: str | None = None
    sdp: str | None = None  # the browser's WebRTC offer (OpenAI)
    retreat_id: str | None = None
    local_time: str | None = None  # the browser's local time with its offset, e.g. 2026-09-26T21:30:00-04:00


class TalkEnd(BaseModel):
    session_id: str
    seconds: int = 0
    transcript: str = ""


@router.post("/session")
async def talk_session(body: TalkRequest, user: User = Depends(current_user)):
    """Start a live conversation with the companion, with the person's notes and the
    retreat (and which days they've listened to) as its context."""
    retreat = await readable_retreat(body.retreat_id, user) if body.retreat_id else None
    about_me = await profile.load(user.id)
    provider = body.provider or talk.options()["default_provider"]
    try:
        return await talk.start(user, retreat, about_me["about"], about_me["companion_notes"], provider or "",
                                body.voice or "", body.sdp, body.local_time, about_me.get("companion_prompt", ""))
    except talk.TalkError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.post("/end")
async def talk_end(body: TalkEnd, user: User = Depends(current_user)):
    """The browser reports the end of a conversation: counts free minutes, logs the transcript."""
    await talk.end(user, body.session_id, body.seconds, body.transcript)
    return {"ok": True}


@router.get("/history")
async def talk_history(user: User = Depends(current_user)):
    """Past conversations (dates, retreat, length, transcript when kept) and the memory summary."""
    return await talk.load_history(user.id)


@router.delete("/history")
async def forget_talks(user: User = Depends(current_user)):
    """Forget every past conversation and the memory summary."""
    await talk.clear_history(user.id)
    return {"ok": True}
