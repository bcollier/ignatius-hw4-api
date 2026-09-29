"""Prayed days on your own calendar. Turning it on makes a private feed address (a
long random token in the URL is the only key); subscribe to it once in Google Calendar,
Apple Calendar or Outlook, and every day you mark prayed appears as an all-day event,
"Prayed · Week 3 · Day 1 · Consideration of the Way Things Are", with links to the app
and to that day. Calendar apps re-read the feed on their own schedule (Google every few
hours, Apple as often as you set). Turning it off makes the old address stop working."""

import secrets
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import config
from ..auth import User, current_user
from ..storage import StorageError, store
from .highlights import _json, _save

router = APIRouter(prefix="/api/calendar")
TOKENS = "_calendar/tokens.json"  # token -> {"user_id", "tz"}
CACHE_SECONDS = 600
_cache: dict[str, tuple[float, bytes]] = {}


class FeedRequest(BaseModel):
    timezone: str = Field("UTC", max_length=64)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _urls(token: str) -> dict:
    https = f"{config.PUBLIC_API_URL.rstrip('/')}/api/calendar/{token}.ics"
    return {"https": https, "webcal": "webcal://" + https.split("://", 1)[1]}


async def _mine(user_id: str) -> tuple[str, dict] | None:
    for token, who in (await _json(TOKENS, {})).items():
        if who.get("user_id") == user_id:
            return token, who
    return None


@router.get("/feed")
async def feed_status(user: User = Depends(current_user)):
    found = await _mine(user.id)
    return {"on": bool(found), **(_urls(found[0]) if found else {})}


@router.post("/feed")
async def turn_on(body: FeedRequest, user: User = Depends(current_user)):
    """The person's feed address (the same one if it's already on)."""
    tokens = await _json(TOKENS, {})
    found = await _mine(user.id)
    token = found[0] if found else secrets.token_urlsafe(24)
    tokens[token] = {"user_id": user.id, "tz": _zone(body.timezone).key}
    await _save(TOKENS, tokens)
    return {"on": True, **_urls(token)}


@router.delete("/feed", status_code=204)
async def turn_off(user: User = Depends(current_user)):
    tokens = await _json(TOKENS, {})
    kept = {t: w for t, w in tokens.items() if w.get("user_id") != user.id}
    if len(kept) != len(tokens):
        await _save(TOKENS, kept)
        _cache.clear()


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


def _fold(line: str) -> str:
    """RFC 5545: lines longer than 75 octets continue on the next line after a space."""
    out, data = [], line.encode()
    while len(data) > 75:
        cut = 75 if not out else 74
        while cut and (data[cut] & 0xC0) == 0x80:  # never split a character
            cut -= 1
        out.append(data[:cut].decode())
        data = data[cut:]
    out.append(data.decode())
    return "\r\n ".join(out)


def day_title(retreat: dict, day: dict) -> str:
    week = len(retreat.get("series") or []) + 1 if retreat.get("series") else None
    parts = [f"Week {week}" if week else (retreat.get("plan") or {}).get("title", "Retreat"), f"Day {day['day']}", day.get("title", "")]
    return "Prayed · " + " · ".join(p for p in parts if p)


def events(retreat: dict, zone: ZoneInfo) -> list[str]:
    plan_days = {str(d["day"]): d for d in (retreat.get("plan") or {}).get("days", [])}
    app = config.APP_URL.rstrip("/") + "/"
    out = []
    for n, state in retreat.get("days", {}).items():
        dates = state.get("prayed_log") or ([state["prayed_at"]] if state.get("prayed_at") else [])
        d = plan_days.get(n)
        if not d:
            continue
        link = f"{app}?r={retreat['id']}&day={n}"
        title = day_title(retreat, d)
        about = (retreat.get("plan") or {}).get("title", "")
        for when in dict.fromkeys(dates):
            local = datetime.fromisoformat(when).astimezone(zone).date()
            out += ["BEGIN:VEVENT", f"UID:{retreat['id']}-{n}-{local:%Y%m%d}@ignatius-at-home",
                    f"DTSTAMP:{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
                    f"DTSTART;VALUE=DATE:{local:%Y%m%d}", f"DTEND;VALUE=DATE:{local + timedelta(days=1):%Y%m%d}",
                    f"SUMMARY:{_escape(title)}", f"URL:{link}",
                    f"DESCRIPTION:{_escape(f'{about}{chr(10)}This day: {link}{chr(10)}Ignatius at Home: {app}')}",
                    "TRANSP:TRANSPARENT", "END:VEVENT"]
    return out


@router.get("/{token}.ics")
async def feed(token: str):
    cached = _cache.get(token)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return Response(cached[1], media_type="text/calendar; charset=utf-8")
    who = (await _json(TOKENS, {})).get(token)
    if not who:
        raise HTTPException(404, "No such calendar.")
    zone = _zone(who.get("tz", "UTC"))
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Ignatius at Home//Prayed days//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:Prayed (Ignatius at Home)", f"X-WR-TIMEZONE:{zone.key}",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for summary in await store.list_for(who["user_id"]):
        try:
            retreat = await store.load(summary["id"])
        except StorageError:
            continue
        if retreat:
            lines += events(retreat, zone)
    lines.append("END:VCALENDAR")
    body = ("\r\n".join(_fold(line) for line in lines) + "\r\n").encode()
    _cache[token] = (time.time(), body)
    return Response(body, media_type="text/calendar; charset=utf-8")
