"""Your own Examen: an end-of-day Examen written around the person's life and
recorded for them (full accounts; see app/my_examen.py)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import my_examen, tts
from ..auth import User, current_user

router = APIRouter(prefix="/api/practice/examen")


class ExamenRequest(BaseModel):
    days: str = ""  # what their days are like, in their words (optional; their notes are used too)
    voice: str = "deluxe"  # "deluxe" (George, ElevenLabs) or "standard" (Ryan, Microsoft)


@router.get("")
async def read_examen(user: User = Depends(current_user)):
    """Their Examen and whether it's ready ("none", "making", "ready" or "failed")."""
    return await my_examen.view(user.id)


@router.post("")
async def make_examen(body: ExamenRequest, user: User = Depends(current_user)):
    if not user.full:
        raise HTTPException(403, "Your own Examen is part of the full version.")
    if body.voice not in my_examen.VOICES:
        raise HTTPException(400, "Choose the deluxe or standard voice.")
    if body.voice == "deluxe" and "premium" not in tts.tiers():
        raise HTTPException(400, "The deluxe voices aren't set up on this server.")
    if len(body.days) > my_examen.MAX_DAYS_TEXT:
        raise HTTPException(400, f"Please keep it under {my_examen.MAX_DAYS_TEXT:,} characters.")
    return await my_examen.start(user.id, user.log_email, body.days, body.voice)
