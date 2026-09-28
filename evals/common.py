"""What both eval systems share: the contestant models, one way to call any of them,
the test set (passages and companion scenarios), and the exact prompts the app sends.

Everything is cached under evals/runs/<run>/ (samples, judgments, scores), so a rerun
only does what's missing. Nothing touches the production database: search status and
logs go to evals/cache.
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Keep everything local, even when .env names the production project. Set before the
# app's config loads .env (load_dotenv doesn't override what's already set).
os.environ["SUPABASE_URL"] = ""
os.environ["DATA_DIR"] = str(HERE / "cache" / "data")
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")  # DeepEval sends nothing home

import httpx  # noqa: E402

from app import config, llm, prompts, search  # noqa: E402

# id: (provider, model, label). Providers: openrouter, jetstream, ollama.
MODELS = {
    "opus": ("openrouter", "anthropic/claude-opus-5.5", "Claude Opus 5.5"),
    "fable": ("openrouter", "anthropic/claude-fable-5.1", "Claude Fable 5.1"),
    "gpt6-sol": ("openrouter", "openai/gpt-6-sol", "OpenAI GPT-6 Sol"),
    "gpt6-astra": ("openrouter", "openai/gpt-6-astra", "OpenAI GPT-6 Astra"),
    "gpt5.5": ("openrouter", "openai/gpt-5.5", "OpenAI GPT-5.5"),
    "gemini": ("openrouter", "google/gemini-3.8-flash", "Google Gemini 3.8 Flash"),
    "muse": ("jetstream", "muse-glimmer", "Muse Glimmer (free, Jetstream2)"),
    "llama-scout": ("jetstream", "llama-4-scout", "Llama 4 Scout (free, Jetstream2)"),
    "gemma": ("ollama", "gemma4:31b", "Gemma 4 31B (local, Ollama)"),
}
DEFAULT_MODELS = "opus,gpt6-sol,muse,gemma"
# Five judges from four companies and the free academic models, so no one judge's taste
# decides. Three are also contestants, so the report also scores each contestant without
# its own model's judgments. Grok judges only in system B (G-Eval), keeping the systems apart.
DEFAULT_JUDGES = "opus,gpt6-sol,gemini,muse,llama-scout"
WORDS = 500  # asked of every model for both tracks (the app scales this to the voice)
MAX_TOKENS = 16_000  # room for reasoning models to think before they write
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_CONTEXT = 32_768  # the deep dive's prompt is about 20,000 tokens
OLLAMA_MAX_OUTPUT = 3_000  # a 500-word script is about 700 tokens; this leaves room, but stops a runaway
TIMEOUT = httpx.Timeout(900, connect=30)  # a local 31B model writes about 6 tokens a second
AT_ONCE = {"openrouter": 6, "jetstream": 2, "ollama": 1}
LIMITS: dict[str, asyncio.Semaphore] = {}

# The rubric: every scale is 1 to 7. For these, lower is better.
LOWER_IS_BETTER = {"ai_jargon", "too_vague", "theological_disagreement"}
SCALES = ["emotionally_engaging", "thoughtful", "well_researched", "encouraging",
          "ai_jargon", "too_vague", "theological_disagreement",
          "love", "joy", "peace", "patience", "kindness", "goodness", "faithfulness", "gentleness", "self_control",
          "faith", "hope", "charity"]
COMPANION_SCALES = ["listening", "one_question", "restraint", "warmth", "spiritual_depth", "safety",
                    "ai_jargon", "too_vague", "theological_disagreement"]


def goodness(scale: str, value: float) -> float:
    """A 1-7 score turned so that higher is always better (for averages)."""
    return 8 - value if scale in LOWER_IS_BETTER else value


# ---------------------------------------------------------------- calling a model

class ModelError(Exception):
    pass


async def chat(model_id: str, system: str, user: str, json_reply: bool = False) -> dict:
    """One completion: {"text", "seconds", "input_tokens", "output_tokens", "usd"}."""
    provider, model, _ = MODELS[model_id]
    limit = LIMITS.setdefault(provider, asyncio.Semaphore(AT_ONCE[provider]))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    async with limit, httpx.AsyncClient(timeout=TIMEOUT) as http:
        started = time.monotonic()
        if provider == "ollama":
            body = {"model": model, "messages": messages, "stream": False, "think": False,
                    "options": {"num_ctx": OLLAMA_CONTEXT, "num_predict": OLLAMA_MAX_OUTPUT}}
            if json_reply:
                body["format"] = "json"
            r = await http.post(f"{OLLAMA_URL}/api/chat", json=body)
            _check(r, model_id)
            data = r.json()
            text, tokens_in, tokens_out, usd = (data["message"]["content"], data.get("prompt_eval_count", 0),
                                                data.get("eval_count", 0), 0.0)
        else:
            body = {"model": model, "messages": messages, "max_tokens": MAX_TOKENS}
            if provider == "openrouter":
                url, key = "https://openrouter.ai/api/v1/chat/completions", config.OPENROUTER_API_KEY
                body["usage"] = {"include": True}  # OpenRouter adds the cost in dollars
            else:
                url, key = f"{config.JETSTREAM_BASE_URL}/chat/completions", config.JETSTREAM_API_KEY
            r = await http.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
            _check(r, model_id)
            data = r.json()
            usage = data.get("usage") or {}
            text = data["choices"][0]["message"].get("content") or ""
            tokens_in, tokens_out, usd = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), usage.get("cost", 0.0)
        seconds = round(time.monotonic() - started, 1)
    if not text.strip():
        raise ModelError(f"{model_id} returned an empty reply")
    return {"text": text, "seconds": seconds, "input_tokens": tokens_in, "output_tokens": tokens_out,
            "usd": round(usd or 0.0, 5)}


def _check(r: httpx.Response, model_id: str) -> None:
    if r.status_code != 200:
        raise ModelError(f"{model_id} answered {r.status_code}: {r.text[:300]}")


def parse_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(match.group(0)) if match else None
    except ValueError:
        return None


def check_models(ids: list[str]) -> None:
    for m in ids:
        if m not in MODELS:
            raise SystemExit(f"Unknown model {m!r}; choose from {', '.join(MODELS)}")


# ---------------------------------------------------------------- the test set

def load_passages(chosen: str | None = None) -> list[dict]:
    passages = json.loads((HERE / "passages.json").read_text())
    if not chosen:
        return passages
    if chosen.isdigit():
        return passages[:int(chosen)]
    return [p for p in passages if p["id"] in chosen.split(",")]


def load_scenarios(chosen: str | None = None) -> list[dict]:
    scenarios = json.loads((HERE / "companion_scenarios.json").read_text())
    if chosen and chosen.isdigit():
        return scenarios[:int(chosen)]
    return [s for s in scenarios if not chosen or s["id"] in chosen.split(",")]


_SCRIPTURE_LOCK = asyncio.Lock()


async def scripture(p: dict) -> str:
    """The passage in the World English Bible (public domain), fetched once, one request at
    a time and patiently: bible-api.com limits how often it can be asked."""
    path = HERE / "cache" / "scripture" / f"{p['id']}.txt"
    async with _SCRIPTURE_LOCK:
        for attempt in range(6):
            if path.exists():
                break
            async with httpx.AsyncClient(timeout=30) as http:
                r = await http.get(f"https://bible-api.com/{p['ref']}", params={"translation": "web"})
            if r.status_code == 429:
                await asyncio.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(" ".join(r.json()["text"].split()))
    return path.read_text()


async def research(p: dict) -> dict:
    """The web research every model's deep dive gets: queries written by the free model
    (as the app does for free retreats), run through all the search services at once."""
    path = HERE / "cache" / "research" / f"{p['id']}.json"
    if not path.exists():
        reply = await chat("muse", prompts.BACKGROUND + "\n\n" + prompts.SEARCH_QUERIES,
                           day_context(p, await scripture(p)))
        queries = llm._queries(reply["text"]) or [p["ref"]]
        found = await search.search(queries, "all")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"queries": queries, "provider": found.provider, "results": found.results},
                                   ensure_ascii=False, indent=1))
    return json.loads(path.read_text())


def day_context(p: dict, text: str, heart: str | None = None) -> str:
    day = {"day": p["day"], "title": p["title"], "source_ref": p["ref"], "grace": p["grace"], "focus": p["focus"],
           "passage_text": text}
    return prompts.day_context(p["retreat"], day, None, heart=heart)


def system_for(track: str, words: int) -> str:
    """The system prompt the app sends (background first), with no About me notes."""
    if track == "heart":
        body = prompts.HEART_PRESETS["companion"] + "\n\n" + prompts.HEART_FIXED.format(words=words)
    else:
        body = llm._deep_system(prompts.DEEP_INSTRUCTIONS, words, has_results=True, search_on=False)
    return prompts.BACKGROUND + "\n\n" + body


# ---------------------------------------------------------------- a run's files

class RunDir:
    def __init__(self, name: str):
        self.dir = HERE / "runs" / name

    def path(self, kind: str, *parts: str) -> Path:
        return self.dir / kind / ("__".join(parts) + ".json")

    def samples(self, kind: str = "samples") -> list[dict]:
        return [json.loads(f.read_text()) for f in sorted((self.dir / kind).glob("*.json"))]

    async def once(self, path: Path, make):
        """The cached result at `path`, or make it and save it."""
        if path.exists():
            return json.loads(path.read_text())
        result = await make()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1))
        return result
