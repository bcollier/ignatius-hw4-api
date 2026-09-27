"""The prayer practice journal: what the person writes during the guided exercises'
journaling pauses (the daily practice, the weekly review, the life's faith story).
Private to them, kept in their folder so it follows them to every device."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import User, current_user
from ..storage import StorageError, store

router = APIRouter(prefix="/api/practice")
MAX_ANSWER = 20_000
MAX_ENTRIES = 2_000


def _path(user_id: str) -> str:
    return f"{user_id}/practice_journal.json"


async def _load(user_id: str) -> list[dict]:
    try:
        return json.loads(await store.get_file(_path(user_id)))
    except (StorageError, ValueError):
        return []


class Entry(BaseModel):
    session: str
    question: str
    answer: str


@router.get("/journal")
async def read_journal(user: User = Depends(current_user)):
    """Everything written in the guided exercises, newest last."""
    return {"entries": await _load(user.id)}


@router.post("/journal")
async def write_entry(body: Entry, user: User = Depends(current_user)):
    answer = body.answer.strip()
    if not answer:
        raise HTTPException(400, "There's nothing to save.")
    if len(answer) > MAX_ANSWER:
        raise HTTPException(400, f"An entry can be up to {MAX_ANSWER:,} characters.")
    entries = await _load(user.id)
    entry = {"at": datetime.now(timezone.utc).isoformat(), "session": body.session[:40],
             "question": body.question[:500], "answer": answer}
    entries = (entries + [entry])[-MAX_ENTRIES:]
    await store.put_file(_path(user.id), json.dumps(entries).encode(), "application/json")
    return entry
