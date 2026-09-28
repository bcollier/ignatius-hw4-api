"""Errors from people's browsers while the app starts, so a failure on someone's phone
can be seen and fixed (index.html's start-up guard sends them; no sign-in needed).
Kept per day in the storage bucket, at most MAX_PER_DAY, and written to the server log."""

import json
import logging
import time
from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..storage import StorageError, store
from .handoff import client_address

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
MAX_PER_DAY = 500
PER_ADDRESS = 20  # reports kept per address per WINDOW; more are dropped quietly
PER_MINUTE = 60  # reports kept from everyone per minute
WINDOW = 10 * 60
_recent: dict[str, deque] = {}
_all: deque = deque()


def _allowed(address: str) -> bool:
    """No sign-in here, so the endpoint can't be used to flood the log or the bucket."""
    now = time.time()
    while _all and now - _all[0] > 60:
        _all.popleft()
    mine = _recent.setdefault(address, deque())
    while mine and now - mine[0] > WINDOW:
        mine.popleft()
    if len(mine) >= PER_ADDRESS or len(_all) >= PER_MINUTE:
        return False
    mine.append(now)
    _all.append(now)
    if len(_recent) > 10_000:  # forget quiet addresses
        for a in [a for a, q in _recent.items() if not q]:
            _recent.pop(a)
    return True


class ClientError(BaseModel):
    message: str = Field("", max_length=1000)
    where: str = Field("", max_length=300)
    version: str = Field("", max_length=20)
    page: str = Field("", max_length=200)
    agent: str = Field("", max_length=300)
    standalone: bool = False


@router.post("/client-error", status_code=204)
async def client_error(body: ClientError, request: Request):
    if not _allowed(client_address(request)):
        return
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
