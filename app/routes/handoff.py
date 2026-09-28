"""Signing in on another device with a short code: the way into an iPhone Home Screen
app, which keeps its own storage and never receives the email's sign-in link (the link
opens in Safari, and a preview of it can use it up).

Signed in somewhere (usually Safari, straight from the email), the person asks for a
code; typed into the other device, the code is exchanged for a fresh one-time sign-in
token made for them by Supabase (admin generate_link, which sends no email), and that
device finishes signing in with it. Codes are eight digits, work once, last
CODE_SECONDS, and each account has one at a time. Wrong guesses are limited per address
(the address the proxy saw, which a caller can't set); a flood of wrong guesses from
many addresses is logged but never blocks someone typing a valid code."""

import logging

import secrets
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import auth, config
from ..auth import User, current_user

router = APIRouter(prefix="/api/handoff")
CODE_SECONDS = 10 * 60
MAX_TRIES = 8  # wrong codes per address per CODE_SECONDS
FLOOD = 200  # wrong codes from everyone per CODE_SECONDS worth a warning in the log
log = logging.getLogger(__name__)

_codes: dict[str, tuple[str, float]] = {}  # code -> (email, expires at)
_wrong: dict[str, list[float]] = {}  # address -> times of wrong codes


class Redeem(BaseModel):
    code: str


@router.post("")
async def new_code(user: User = Depends(current_user)):
    """A code for signing in on another device as this person."""
    if not auth.enabled():
        raise HTTPException(400, "Sign-in codes need the sign-in service, which isn't set up here.")
    if user.anonymous or not user.email:
        raise HTTPException(400, "A guest session can't be moved to another device. Sign in with your email first.")
    _forget_expired()
    for old in [c for c, (email, _) in _codes.items() if email == user.email]:
        _codes.pop(old)  # one code per account: a new one cancels the last
    code = f"{secrets.randbelow(10**8):08d}"
    while code in _codes:
        code = f"{secrets.randbelow(10**8):08d}"
    _codes[code] = (user.email, time.time() + CODE_SECONDS)
    return {"code": code, "expires_in": CODE_SECONDS}


@router.post("/redeem")
async def redeem(body: Redeem, request: Request):
    """Exchange a code for a one-time sign-in token (the browser then calls Supabase's
    verifyOtp with it)."""
    if not auth.enabled():
        raise HTTPException(400, "Sign-in codes need the sign-in service, which isn't set up here.")
    address = client_address(request)
    _check_tries(address)
    _forget_expired()
    code = "".join(ch for ch in body.code if ch.isdigit())
    found = _codes.pop(code, None)
    if not found:
        _wrong.setdefault(address, []).append(time.time())
        if sum(len(v) for v in _wrong.values()) == FLOOD:
            log.warning("handoff: %s wrong codes in ten minutes from %s addresses", FLOOD, len(_wrong))
        raise HTTPException(400, "That code didn't work. Codes last ten minutes and work once; get a new one in your browser.")
    return {"token_hash": await _sign_in_token(found[0]), "type": "magiclink"}


def _forget_expired() -> None:
    now = time.time()
    for code in [c for c, (_, until) in _codes.items() if until < now]:
        _codes.pop(code, None)
    for address in list(_wrong):
        _wrong[address] = [t for t in _wrong[address] if now - t < CODE_SECONDS]
        if not _wrong[address]:
            _wrong.pop(address)


def client_address(request: Request) -> str:
    """The caller's address as Render's edge saw it. Those headers are set by the proxy
    and overwrite anything the caller sends; the first entry of X-Forwarded-For is the
    caller's own claim, so it's used only as a last resort, from its end (the entry the
    nearest proxy added)."""
    for header in ("cf-connecting-ip", "true-client-ip"):
        if request.headers.get(header):
            return request.headers[header].strip()
    forwarded = [a.strip() for a in request.headers.get("x-forwarded-for", "").split(",") if a.strip()]
    return forwarded[-1] if forwarded else (request.client.host if request.client else "?")


def _check_tries(address: str) -> None:
    _forget_expired()
    if len(_wrong.get(address, [])) >= MAX_TRIES:
        raise HTTPException(429, "Too many wrong codes. Wait ten minutes, then try again with a new code.")


async def _sign_in_token(email: str) -> str:
    """A fresh one-time magic-link token for the person, made by Supabase without an email."""
    key = config.SUPABASE_SECRET_KEY
    async with httpx.AsyncClient(timeout=20) as http:
        response = await http.post(f"{config.SUPABASE_URL}/auth/v1/admin/generate_link",
                                   headers={"apikey": key, "Authorization": f"Bearer {key}"},
                                   json={"type": "magiclink", "email": email})
    token = response.json().get("hashed_token") if response.status_code == 200 else None
    if not token:
        raise HTTPException(502, "The sign-in service didn't answer. Please try again.")
    return token
