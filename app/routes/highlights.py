"""Highlights: words a person marks while praying or reading (the passage, a reflection,
a deep dive), kept in their own folder so they follow them to every device, listed in
Settings. Optionally one comes back to them each week by email or text message: the
one sent longest ago (or never), so the whole list comes round in turn."""

import html
import hmac
import json
import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .. import config, notify
from ..auth import User, current_user
from ..storage import StorageError, store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/highlights")
MAX_TEXT = 1200
MAX_HIGHLIGHTS = 3000
WEEKLY = "_highlights/weekly.json"  # who asked for the weekly highlight, and where to send it
PHONE = re.compile(r"^\+[1-9]\d{7,14}$")  # E.164


def _path(user_id: str) -> str:
    return f"{user_id}/highlights.json"


async def _json(path: str, empty):
    try:
        return json.loads(await store.get_file(path))
    except (StorageError, ValueError):
        return empty


async def _save(path: str, data) -> None:
    await store.put_file(path, json.dumps(data).encode(), "application/json")


class NewHighlight(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    retreat_id: str = Field("", max_length=64)
    retreat_title: str = Field("", max_length=200)
    day: int | None = Field(None, ge=0, le=400)
    part: str = Field("", max_length=40)
    ref: str = Field("", max_length=120)


class Weekly(BaseModel):
    email: bool = False
    sms: bool = False
    phone: str = Field("", max_length=20)


def _channels() -> dict:
    return {"email": notify.email_ready(), "sms": notify.sms_ready()}


@router.get("")
async def list_highlights(user: User = Depends(current_user)):
    """Newest first, with this person's weekly choice and which ways of sending are set up."""
    items = await _json(_path(user.id), [])
    mine = (await _json(WEEKLY, {})).get(user.id, {})
    return {"highlights": items[::-1],  # saved in order, so newest first
            "weekly": {"email": bool(mine.get("email_on")), "sms": bool(mine.get("sms_on")), "phone": mine.get("phone", "")},
            "channels": _channels(), "email": user.email}


@router.post("")
async def add(body: NewHighlight, user: User = Depends(current_user)):
    text = " ".join(body.text.split())
    if len(text) < 2:
        raise HTTPException(400, "Choose a few words to highlight.")
    items = await _json(_path(user.id), [])
    same = next((h for h in items if h["text"] == text and h.get("retreat_id") == body.retreat_id), None)
    if same:
        return same
    item = {"id": uuid.uuid4().hex[:12], "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **body.model_dump(), "text": text, "sent_at": None}
    await _save(_path(user.id), (items + [item])[-MAX_HIGHLIGHTS:])
    return item


@router.delete("/{highlight_id}", status_code=204)
async def remove(highlight_id: str, user: User = Depends(current_user)):
    items = await _json(_path(user.id), [])
    kept = [h for h in items if h["id"] != highlight_id]
    if len(kept) == len(items):
        raise HTTPException(404, "No such highlight.")
    await _save(_path(user.id), kept)


@router.put("/weekly")
async def set_weekly(body: Weekly, user: User = Depends(current_user)):
    phone = re.sub(r"[\s().-]", "", body.phone)
    if body.sms and not PHONE.match(phone):
        raise HTTPException(400, "Enter the phone number with its country code, like +1 412 555 0100.")
    if body.email and not user.email:
        raise HTTPException(400, "Sign in with an email address to get the weekly highlight by email.")
    if (body.email and not notify.email_ready()) or (body.sms and not notify.sms_ready()):
        raise HTTPException(400, "That way of sending isn't set up on this server yet.")
    everyone = await _json(WEEKLY, {})
    if body.email or body.sms:
        everyone[user.id] = {"email": user.email, "email_on": body.email, "sms_on": body.sms, "phone": phone if body.sms else ""}
    else:
        everyone.pop(user.id, None)
    await _save(WEEKLY, everyone)
    return {"email": body.email, "sms": body.sms, "phone": phone if body.sms else ""}


def _message(h: dict) -> tuple[str, str, str]:
    where = " · ".join(x for x in (h.get("retreat_title"), f"Day {h['day']}" if h.get("day") else "", h.get("ref")) if x)
    text = f"“{h['text']}”\n\n{where}\n\nA highlight you saved in Ignatius at Home. {config.APP_URL}"
    body = (f"<p style=\"font-family:Georgia,serif;font-size:20px;line-height:1.5\">“{html.escape(h['text'])}”</p>"
            f"<p style=\"font-family:Georgia,serif;color:#6b5d4c\">{html.escape(where)}</p>"
            f"<p style=\"font-family:Georgia,serif;color:#6b5d4c;font-size:14px\">A highlight you saved in "
            f"<a href=\"{html.escape(config.APP_URL)}\">Ignatius at Home</a>. To stop these, untick them in Settings.</p>")
    return "Your highlight for this week", text, body


@router.post("/send-weekly")
async def send_weekly(x_cron_secret: str = Header("")):
    """Called once a week by the scheduled job: one highlight to each person who asked."""
    if not config.CRON_SECRET or not hmac.compare_digest(x_cron_secret, config.CRON_SECRET):
        raise HTTPException(403, "Not allowed.")
    everyone = await _json(WEEKLY, {})
    sent = skipped = failed = 0
    for user_id, who in everyone.items():
        items = await _json(_path(user_id), [])
        if not items:
            skipped += 1
            continue
        h = min(items, key=lambda x: (x.get("sent_at") or "", x["at"]))  # never sent first, then the longest ago
        subject, text, body = _message(h)
        ok = False
        try:
            if who.get("email_on") and who.get("email") and notify.email_ready():
                await notify.send_email(who["email"], subject, text, body)
                ok = True
            if who.get("sms_on") and who.get("phone") and notify.sms_ready():
                await notify.send_sms(who["phone"], text[:600])
                ok = True
        except Exception:
            log.exception("couldn't send the weekly highlight to %s", user_id)
            failed += 1
            continue
        if ok:
            h["sent_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            await _save(_path(user_id), items)
            sent += 1
        else:
            skipped += 1
    return {"sent": sent, "skipped": skipped, "failed": failed}
