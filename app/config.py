"""Settings read from environment variables (and a local .env file in development)."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()

# Which backend writes the text: "openrouter", "anthropic", or "stub" (no API calls,
# used for tests and for building the frontend before a key is available).
if os.environ.get("LLM_MODE"):
    LLM_MODE = os.environ["LLM_MODE"]
elif OPENROUTER_API_KEY:
    LLM_MODE = "openrouter"
elif ANTHROPIC_API_KEY:
    LLM_MODE = "anthropic"
else:
    LLM_MODE = "stub"

# OpenRouter names Claude models "anthropic/<id>"; the Anthropic API uses the bare id.
LLM_MODEL = os.environ.get(
    "LLM_MODEL", "anthropic/claude-opus-5" if LLM_MODE == "openrouter" else "claude-opus-5"
)
WEB_SEARCH = os.environ.get("WEB_SEARCH", "1") == "1"

ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")

# Supabase: sign-in, the retreats table and file storage. Leave unset to run
# locally with no sign-in and files on disk.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_PUBLISHABLE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()  # public; sent to the browser
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "").strip()  # server only
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "retreats")

# Who may sign in and spend API credit. Empty means anyone with an account.
ALLOWED_EMAILS = [e.lower() for e in _list("ALLOWED_EMAILS", "")]

ALLOWED_ORIGINS = _list(
    "ALLOWED_ORIGINS",
    "http://localhost:5500,http://127.0.0.1:5500,http://localhost:8080,http://127.0.0.1:8080",
)

DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp/ignatius"))

MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 15)
MAX_PAGES = _int("MAX_PAGES", 40)
MAX_SOURCE_CHARS = _int("MAX_SOURCE_CHARS", 80_000)
MAX_IMAGES = _int("MAX_IMAGES", 8)
MAX_SCANNED_PAGES = _int("MAX_SCANNED_PAGES", 4)
MAX_DAYS = _int("MAX_DAYS", 14)
DEFAULT_DAYS = _int("DEFAULT_DAYS", 7)

# Length caps per audio track, in characters of script. ElevenLabs bills per
# character, so the premium tier gets a tighter cap by default.
MAX_TRACK_CHARS = _int("MAX_TRACK_CHARS", 6000)
PREMIUM_MAX_TRACK_CHARS = _int("PREMIUM_MAX_TRACK_CHARS", 2500)

MAX_CONCURRENT_JOBS = _int("MAX_CONCURRENT_JOBS", 2)
