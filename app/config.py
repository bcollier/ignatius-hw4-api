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

# The default model, by its OpenRouter id (mapped to the Anthropic id when calling
# Anthropic directly). Users can pick another from pricing.MODELS.
LLM_MODEL = os.environ.get("LLM_MODEL", "anthropic/claude-opus-5")
WEB_SEARCH = os.environ.get("WEB_SEARCH", "1") == "1"

ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")
# What an ElevenLabs character costs depends on the plan; this is for estimates.
ELEVENLABS_USD_PER_1K_CHARS = float(os.environ.get("ELEVENLABS_USD_PER_1K_CHARS", "0.30"))

# Supabase: sign-in, the retreats table and file storage. Leave unset to run
# locally with no sign-in and files on disk.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_PUBLISHABLE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()  # public; sent to the browser
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "").strip()  # server only
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "retreats")

# Jetstream2 inference service (academic, no per-token cost), through its Open
# WebUI proxy, which is OpenAI-compatible and reachable from outside Jetstream.
JETSTREAM_API_KEY = os.environ.get("JETSTREAM_API_KEY", "").strip()
JETSTREAM_BASE_URL = os.environ.get("JETSTREAM_BASE_URL", "https://llm.jetstream-cloud.org/api").rstrip("/")
JETSTREAM_MODELS = _list("JETSTREAM_MODELS", "llama-4-scout,muse-glimmer")

# Tavily web search for free-mode deep dives (the Jetstream models can't search).
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
TAVILY_SEARCH_DEPTH = os.environ.get("TAVILY_SEARCH_DEPTH", "basic")  # basic = 1 credit, advanced = 2
EXA_API_KEY = os.environ.get("EXA_API_KEY", "").strip()
BRAVE_SEARCH_API_KEY = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()  # Brave "Search" plan
BRAVE_ANSWERS_API_KEY = os.environ.get("BRAVE_ANSWERS_API_KEY", "").strip()  # Brave "Answers" plan (separate key)
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "").strip()
LINKUP_API_KEY = os.environ.get("LINKUP_API_KEY", "").strip()  # Linkup search and deep research
# Default research service for free mode: brave, exa, tavily, firecrawl, linkup,
# linkup_deep or brave_answers
# (falls back to the first one with a key). Users can pick another on the page.
SEARCH_PROVIDER = os.environ.get("SEARCH_PROVIDER", "brave")

# Who gets the full app (Claude, web search, ElevenLabs). Empty means everyone.
ALLOWED_EMAILS = [e.lower() for e in _list("ALLOWED_EMAILS", "")]
# Everyone else, including anonymous "try it" sessions, gets free mode: Jetstream
# models and free voices only, with a cap on retreats. Off when there is no
# Jetstream key, in which case people not on ALLOWED_EMAILS are refused.
FREE_MODE = os.environ.get("FREE_MODE", "1") == "1" and bool(JETSTREAM_API_KEY)
FREE_MAX_RETREATS = _int("FREE_MAX_RETREATS", 3)

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
