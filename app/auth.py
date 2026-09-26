"""Who is calling. With Supabase configured, the browser signs in with Supabase Auth
and sends its access token; the backend asks Supabase who the token belongs to.
Without Supabase (tests, local development) every request is one local user."""

import os
import time
from dataclasses import dataclass

import httpx
from fastapi import Header, HTTPException

from . import config

LOCAL_USER_ID = "00000000-0000-0000-0000-000000000000"
_cache: dict[str, tuple["User", float]] = {}  # token -> (user, checked_at)
CACHE_SECONDS = 300


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


def email_allowed(email: str) -> bool:
    return not config.ALLOWED_EMAILS or email.lower() in config.ALLOWED_EMAILS


async def current_user(authorization: str = Header(default="")) -> User:
    if not enabled():
        # Local development: one user; LOCAL_USER_MODE=free previews free mode.
        return User(LOCAL_USER_ID, "local", full=os.environ.get("LOCAL_USER_MODE") != "free")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Sign in to continue.")
    cached = _cache.get(token)
    if cached and time.time() - cached[1] < CACHE_SECONDS:
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
    _cache[token] = (user, time.time())
    return user
