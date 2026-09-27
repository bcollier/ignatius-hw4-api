"""Talk it over: starting and ending a live conversation, and its remembered history."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import profile, talk, tts
from ..access import readable_retreat
from ..auth import User, current_user

router = APIRouter(prefix="/api/talk")


class TalkRequest(BaseModel):
    provider: str | None = None
    voice: str | None = None
    brain: str | None = None  # taking turns: which model writes the replies
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
                                body.voice or "", body.sdp, body.local_time, about_me.get("companion_prompt", ""),
                                body.brain or "")
    except talk.TalkError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


class TurnRequest(BaseModel):
    session_id: str
    text: str = Field("", max_length=4000)


@router.post("/turn")
async def talk_turn(body: TurnRequest, user: User = Depends(current_user)):
    """Taking turns: what the person said, and the companion's reply."""
    try:
        return {"reply": await talk.turn(user, body.session_id, body.text)}
    except talk.TalkError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.post("/speak")
async def talk_speak(body: TurnRequest, user: User = Depends(current_user)):
    """Taking turns: one sentence of the reply, spoken, as MP3."""
    try:
        audio = await talk.speak(user, body.session_id, body.text)
    except talk.TalkError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except tts.TTSError as exc:
        raise HTTPException(502, str(exc)) from exc
    return Response(audio, media_type="audio/mpeg")


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
