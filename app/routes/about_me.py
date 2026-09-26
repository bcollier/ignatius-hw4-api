"""About me ("user info.md"): what the person has told the app about themselves."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from .. import config, llm_log, profile
from ..auth import User, current_user
from ..extract import ExtractError, extract
from .uploads import read_upload

router = APIRouter(prefix="/api/profile")
TEXT_SUFFIXES = (".txt", ".md", ".markdown")


class ProfileRequest(BaseModel):
    about: str | None = None
    companion_notes: str | None = None


@router.get("")
async def read_profile(user: User = Depends(current_user)):
    """What the person has told the app about themselves ("user info.md")."""
    return await profile.load(user.id)


@router.put("")
async def write_profile(body: ProfileRequest, user: User = Depends(current_user)):
    llm_log.tag(user_id=user.id, email=user.log_email)  # a long text is condensed by a model
    return await profile.save(user.id, body.about, body.companion_notes, user.full)


@router.post("/upload")
async def upload_profile(file: UploadFile = File(...), user: User = Depends(current_user)):
    """Replace the about-me notes with a text, Markdown, Word or PDF file."""
    data = await read_upload(file)
    text = _text_of(file.filename or "notes", data)
    if not text.strip():
        raise HTTPException(400, "No text found in that file.")
    llm_log.tag(user_id=user.id, email=user.log_email)
    return await profile.save(user.id, text, None, user.full, source=file.filename or "upload")


def _text_of(filename: str, data: bytes) -> str:
    if filename.lower().endswith(TEXT_SUFFIXES):
        text = data.decode("utf-8", errors="replace")
    else:
        try:
            text = extract(filename, data).text
        except ExtractError as exc:
            raise HTTPException(400, "Upload a text (.txt or .md), Word (.docx) or PDF file.") from exc
    return text.replace("[Page ", "\n[Page ")  # keep PDF page markers on their own lines
