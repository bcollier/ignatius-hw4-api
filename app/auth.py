"""Who is calling. With Supabase configured, the browser signs in with Supabase Auth
and sends its access token; the backend asks Supabase who the token belongs to.
Without Supabase (tests, local development) every request is one local user."""

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass

import httpx
from fastapi import Header, HTTPException

from . import config

LOCAL_USER_ID = "00000000-0000-0000-0000-000000000000"
# Who a token belongs to, so every request needn't ask Supabase: keyed by a hash of the
# token (never the token itself), kept for CACHE_SECONDS or until the token expires,
# whichever comes first, and never more than CACHE_MAX entries.
_cache: dict[str, tuple["User", float]] = {}  # sha256(token) -> (user, good until)
CACHE_SECONDS = 300
CACHE_MAX = 5_000


@dataclass(frozen=True)
class User:
    id: str
    email: str
    full: bool = True  # False: free mode (Jetstream models and the free voices)
    anonymous: bool = False

    @property
    def log_email(self) -> str | None:
        """How this person appears in the llm_calls log: their email, or "guest"."""
        return self.email or ("guest" if self.anonymous else None)


def enabled() -> bool:
    return bool(config.SUPABASE_URL and config.SUPABASE_SECRET_KEY)


def check_setup() -> None:
    """At startup: sign-in fully set up, or local mode asked for by name. Anything in
    between (a missing key, say) stops the server rather than letting everyone in."""
    if enabled() or config.LOCAL_MODE:
        return
    if config.SUPABASE_URL or config.SUPABASE_SECRET_KEY:
        raise RuntimeError("Sign-in is half set up: SUPABASE_URL and SUPABASE_SECRET_KEY are both needed.")
    raise RuntimeError("Sign-in isn't set up. Set SUPABASE_URL and SUPABASE_SECRET_KEY, or LOCAL_MODE=1 "
                       "to run on this computer with one local user.")


def email_allowed(email: str) -> bool:
    """Full (paid) mode: the emails on ALLOWED_EMAILS. An empty list means no one, unless
    EVERYONE_FULL=1 says, on purpose, that every signed-in account may spend."""
    if not config.ALLOWED_EMAILS:
        return os.environ.get("EVERYONE_FULL") == "1"
    return email.lower() in config.ALLOWED_EMAILS


def _expires(token: str) -> float:
    """The token's own expiry (its exp claim), read without trusting it for anything but
    shortening how long it's cached. Unknown: now plus CACHE_SECONDS."""
    try:
        payload = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return time.time() + CACHE_SECONDS


def _remember(key: str, user: "User", token: str) -> None:
    now = time.time()
    for k in [k for k, (_, until) in _cache.items() if until <= now]:
        _cache.pop(k, None)
    while len(_cache) >= CACHE_MAX:
        _cache.pop(next(iter(_cache)))  # the oldest
    _cache[key] = (user, min(now + CACHE_SECONDS, _expires(token)))


async def current_user(authorization: str = Header(default="")) -> User:
    if not enabled():
        if not config.LOCAL_MODE:  # never reached after check_setup; a second line of defense
            raise HTTPException(503, "Sign-in isn't set up on this server.")
        # Local development: one user; LOCAL_USER_MODE=free previews free mode.
        return User(LOCAL_USER_ID, "local", full=os.environ.get("LOCAL_USER_MODE") != "free")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Sign in to continue.")
    key = hashlib.sha256(token.encode()).hexdigest()
    cached = _cache.get(key)
    if cached and time.time() < cached[1]:
        return cached[0]

    try:
        async with httpx.AsyncClient(timeout=15) as http:
            response = await http.get(
                f"{config.SUPABASE_URL.rstrip('/')}/auth/v1/user",
                headers={"apikey": config.SUPABASE_PUBLISHABLE_KEY or config.SUPABASE_SECRET_KEY, "Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Couldn't reach the sign-in service.") from exc
    if response.status_code != 200:
        raise HTTPException(401, "Your sign-in has expired. Sign in again.")

    data = response.json()
    email = (data.get("email") or "").lower()
    anonymous = bool(data.get("is_anonymous"))
    full = bool(email) and not anonymous and email_allowed(email)
    if not full and not config.FREE_MODE:
        raise HTTPException(403, "This account isn't on the list of allowed users for this demo.")
    user = User(data["id"], email, full=full, anonymous=anonymous)
    _remember(key, user, token)
    return user
