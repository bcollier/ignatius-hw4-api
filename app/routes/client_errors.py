"""Errors from people's browsers while the app starts, so a failure on someone's phone
can be seen and fixed (index.html's start-up guard sends them; no sign-in needed).
Kept per day in the storage bucket, at most MAX_PER_DAY, and written to the server log."""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..storage import StorageError, store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
MAX_PER_DAY = 500


class ClientError(BaseModel):
    message: str = Field("", max_length=1000)
    where: str = Field("", max_length=300)
    version: str = Field("", max_length=20)
    page: str = Field("", max_length=200)
    agent: str = Field("", max_length=300)
    standalone: bool = False


@router.post("/client-error", status_code=204)
async def client_error(body: ClientError):
    now = datetime.now(timezone.utc)
    log.warning("client error v%s %s %s | %s", body.version, body.where, body.message, body.agent)
    path = f"_client_errors/{now:%Y-%m-%d}.json"
    try:
        entries = json.loads(await store.get_file(path))
    except (StorageError, ValueError):
        entries = []
    if len(entries) < MAX_PER_DAY:
        entries.append({"at": now.isoformat(), **body.model_dump()})
        try:
            await store.put_file(path, json.dumps(entries).encode(), "application/json")
        except StorageError:
            log.exception("couldn't keep a client error")
