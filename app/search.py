"""Web research for free-mode deep dives. The Jetstream models can't search, so the
server runs the queries and hands the model the results, each as
{title, url, content}. The model may cite only these URLs.

Services (each on when its key is set):
  tavily         Tavily search                          TAVILY_API_KEY
  exa            Exa search, with page highlights       EXA_API_KEY
  brave          Brave Search API, web results          BRAVE_SEARCH_API_KEY
  brave_answers  Brave Answers: a cited written answer  BRAVE_ANSWERS_API_KEY
  firecrawl      Firecrawl search                       FIRECRAWL_API_KEY
  linkup         Linkup search (standard depth)         LINKUP_API_KEY
  linkup_deep    Linkup deep research: sourced answer   LINKUP_API_KEY (same account)

Research must never break a build. Every call has a timeout and every error is
caught. A service that reports it is out of credits is skipped until the start of
next month; rate limits, rejected keys and repeated failures pause it for a while.
Those pauses are saved to storage so they survive restarts. If the chosen service
is paused or fails, the next available one is tried; if none work, the deep dive
is written without search. Every call, successful or not, is logged in llm_calls.
"""

import asyncio
import html
import json
import logging
import re
import time
from datetime import datetime, timezone

import httpx

from . import config, llm_log

log = logging.getLogger(__name__)

MAX_RESULTS_PER_QUERY = 4
MAX_RESULTS_TOTAL = 12
SNIPPET_CHARS = 1200
QUERY_TIMEOUT = 20  # seconds per query
STATUS_PATH = "system/search_status.json"

PROVIDERS = {
    "all": "All services, combined",
    "brave": "Brave Search",
    "exa": "Exa",
    "tavily": "Tavily",
    "firecrawl": "Firecrawl",
    "linkup": "Linkup Search",
    "linkup_deep": "Linkup Deep Research (slower, sourced answers)",
    "brave_answers": "Brave Answers (cited summaries)",
}
# "all" runs every configured service at once and merges the results; deep research
# is left out because it takes about 20 seconds a question.
COMBINED_SKIP = {"all", "linkup_deep"}
MAX_RESULTS_COMBINED = 20

# Services that share one account (and so one credit balance) pause together.
ACCOUNT = {"linkup_deep": "linkup"}
# Seconds per query; deep research runs several searches itself.
TIMEOUTS = {"linkup_deep": 120, "brave_answers": 60}

# provider -> {"until": unix time, "reason": str, "failures": int}
_status: dict[str, dict] = {}
_status_loaded = False
_status_lock = asyncio.Lock()


class OutOfCredits(Exception):
    pass


class Paused(Exception):
    def __init__(self, reason: str, seconds: int):
        super().__init__(reason)
        self.reason, self.seconds = reason, seconds


def _keys() -> dict[str, str]:
    return {
        "tavily": config.TAVILY_API_KEY,
        "exa": config.EXA_API_KEY,
        "brave": config.BRAVE_SEARCH_API_KEY,
        "brave_answers": config.BRAVE_ANSWERS_API_KEY,
        "firecrawl": config.FIRECRAWL_API_KEY,
        "linkup": config.LINKUP_API_KEY,
        "linkup_deep": config.LINKUP_API_KEY,
        "all": "",
    }


def configured() -> dict[str, str]:
    """Services with a key, whether or not they're paused. "all" is offered when two or more are."""
    keys = _keys()
    services = {k: label for k, label in PROVIDERS.items() if k != "all" and keys[k]}
    if sum(1 for k in services if k not in COMBINED_SKIP) >= 2:
        return {"all": PROVIDERS["all"], **services}
    return services


def paused(provider: str) -> dict | None:
    if provider == "all":
        return None
    s = _status.get(ACCOUNT.get(provider, provider))
    return s if s and s.get("until", 0) > time.time() else None


def available() -> dict[str, str]:
    """Services with a key that aren't paused."""
    return {k: v for k, v in configured().items() if not paused(k)}


def status() -> dict[str, dict]:
    """For the page: each configured service and whether it's paused, and why."""
    out = {}
    for k, label in configured().items():
        p = paused(k)
        out[k] = {"label": label, "paused": bool(p), "reason": p["reason"] if p else None,
                  "until": datetime.fromtimestamp(p["until"], timezone.utc).isoformat() if p else None}
    return out


def default_provider() -> str | None:
    options = configured()
    return config.SEARCH_PROVIDER if config.SEARCH_PROVIDER in options else next(iter(options), None)


async def _combined(http: httpx.AsyncClient, queries: list[str], research: "Research") -> bool:
    """Run the queries through every available service at once and interleave the
    results (one from each service in turn), so the sources are varied."""
    names = [n for n in configured() if n not in COMBINED_SKIP and not paused(n)]
    parts = {n: Research() for n in names}
    found = await asyncio.gather(*(_run_provider(http, n, queries, parts[n]) for n in names), return_exceptions=True)
    lists = [parts[n].results for n in names]
    for i in range(max((len(x) for x in lists), default=0)):
        for results in lists:
            if i < len(results) and len(research.results) < MAX_RESULTS_COMBINED:
                research.add(**{k: results[i][k] for k in ("title", "url", "content")}, limit=MAX_RESULTS_COMBINED,
                             service=results[i].get("service"))
    for n in names:
        research.queries += parts[n].queries
        research.usd += parts[n].usd
        research.skipped += parts[n].skipped
    research.contributors = [n for n, ok in zip(names, found) if ok is True and parts[n].results]
    return bool(research.results)


def enabled() -> bool:
    return bool(configured())


def _next_month() -> float:
    now = datetime.now(timezone.utc)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc).timestamp()


async def _load_status() -> None:
    global _status_loaded
    if _status_loaded:
        return
    from .storage import store

    try:
        _status.update(json.loads(await store.get_file(STATUS_PATH)))
    except Exception:
        pass  # no saved status yet
    _status_loaded = True


async def _save_status() -> None:
    from .storage import store

    try:
        await store.put_file(STATUS_PATH, json.dumps(_status).encode(), "application/json")
    except Exception:
        log.warning("couldn't save search service status")


async def _pause(provider: str, reason: str, until: float) -> None:
    provider = ACCOUNT.get(provider, provider)
    async with _status_lock:
        _status[provider] = {"until": until, "reason": reason, "failures": 0}
        log.warning("search service %s paused until %s: %s", provider, datetime.fromtimestamp(until, timezone.utc), reason)
        await _save_status()


async def _failed(provider: str) -> None:
    provider = ACCOUNT.get(provider, provider)
    s = _status.setdefault(provider, {"until": 0, "reason": None, "failures": 0})
    s["failures"] = s.get("failures", 0) + 1
    if s["failures"] >= 3:
        await _pause(provider, "failed three times in a row", time.time() + 600)


def _ok(provider: str) -> None:
    provider = ACCOUNT.get(provider, provider)
    if provider in _status and not paused(provider):
        _status[provider]["failures"] = 0


CREDIT_WORDS = re.compile(r"quota|credit|usage limit|plan limit|exceeds? your|insufficient|payment|billing", re.I)


def _check(response: httpx.Response) -> None:
    """Raise OutOfCredits or Paused for responses that mean 'stop using this service'."""
    code, body = response.status_code, response.text[:500]
    if code in (402, 432, 433) or (code in (403, 429) and CREDIT_WORDS.search(body)):
        raise OutOfCredits(f"out of credits ({code})")
    if code == 429:
        raise Paused("rate limited", 60)
    if code in (401, 403):
        raise Paused("key rejected", 3600)
    response.raise_for_status()


class Research:
    """What a search run found, which service found it, and what it cost."""

    def __init__(self):
        self.results: list[dict] = []
        self.queries = 0
        self.usd = 0.0
        self.provider: str | None = None
        self.skipped: list[str] = []  # services dropped during this run, with why
        self.query_texts: list[str] = []
        self.contributors: list[str] = []  # with "all": the services that returned results

    def add(self, title: str | None, url: str | None, content: str | None, limit: int = MAX_RESULTS_TOTAL,
            service: str | None = None) -> bool:
        if not url or url in {r["url"] for r in self.results} or len(self.results) >= limit:
            return False
        self.results.append({"title": title or url, "url": url, "content": (content or "")[:SNIPPET_CHARS],
                             "service": service})
        return True


async def search(queries: list[str], provider: str | None = None) -> Research:
    """Run the queries with the chosen service, falling back to the others. Never raises."""
    research = Research()
    try:
        await _load_status()
        async with httpx.AsyncClient(timeout=QUERY_TIMEOUT) as http:
            if provider == "all":
                if await _combined(http, queries, research):
                    research.provider = "all"
                return research
            order = [provider] if provider in configured() else []
            order += [p for p in configured() if p not in order and p != "all"]
            for name in order:
                if paused(name):
                    research.skipped.append(f"{name}: {paused(name)['reason']}")
                    continue
                found = await _run_provider(http, name, queries, research)
                if found:
                    research.provider = name
                    break
    except Exception:  # research is optional; a bug here must not stop the build
        log.exception("research failed")
    return research


async def _run_provider(http: httpx.AsyncClient, name: str, queries: list[str], research: Research) -> bool:
    run = {
        "tavily": _tavily, "exa": _exa, "brave": _brave, "brave_answers": _brave_answers,
        "firecrawl": _firecrawl, "linkup": _linkup, "linkup_deep": _linkup_deep,
    }[name]
    timeout = TIMEOUTS.get(name, QUERY_TIMEOUT)
    found = False
    for query in queries:
        timer = llm_log.Timer()
        added, meta, error = [], {}, None
        try:
            items, meta = await asyncio.wait_for(run(http, query), timeout + 5)
            added = [r for r in items if research.add(r["title"], r["url"], r["content"], service=name)]
            research.queries += 1
            research.usd += meta.get("usd", 0.0)
            found = found or bool(added)
            _ok(name)
        except OutOfCredits as exc:
            error = str(exc)
            await _pause(name, "out of monthly credits", _next_month())
        except Paused as exc:
            error = exc.reason
            await _pause(name, exc.reason, time.time() + exc.seconds)
        except Exception as exc:  # timeouts, network errors, unexpected response shapes
            error = f"{type(exc).__name__}: {exc}"[:300]
            await _failed(name)
        await llm_log.record(
            provider=name,
            model=meta.get("endpoint", name),
            system="",
            messages=[{"role": "user", "content": query}],
            response_text="\n\n".join(f"{r['title']}\n{r['url']}\n{r['content']}" for r in added) or meta.get("answer"),
            response_extra={"results": added, **{k: v for k, v in meta.items() if k not in ("answer", "endpoint")}},
            usage={"web_searches": 1, "usd": meta.get("usd", 0.0)},
            duration_ms=timer.ms,
            error=error,
        )
        if error and paused(name):
            research.skipped.append(f"{name}: {paused(name)['reason']}")
            break  # try the next service
    return found


def _item(title, url, content) -> dict:
    return {"title": _strip_tags(title), "url": url, "content": _strip_tags(content)}


async def _tavily(http: httpx.AsyncClient, query: str):
    r = await http.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
        json={"query": query, "search_depth": config.TAVILY_SEARCH_DEPTH, "max_results": MAX_RESULTS_PER_QUERY},
    )
    _check(r)
    data = r.json()
    items = [_item(i.get("title"), i.get("url"), i.get("content")) for i in data.get("results", [])]
    return items, {"endpoint": "tavily/search", "credits": (data.get("usage") or {}).get("credits")}


async def _exa(http: httpx.AsyncClient, query: str):
    r = await http.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": config.EXA_API_KEY},
        json={"query": query, "type": "auto", "numResults": MAX_RESULTS_PER_QUERY,
              "contents": {"highlights": {"maxCharacters": SNIPPET_CHARS}}},
    )
    _check(r)
    data = r.json()
    items = [
        _item(i.get("title"), i.get("url"), " … ".join(i.get("highlights") or []) or i.get("summary") or i.get("text"))
        for i in data.get("results", [])
    ]
    return items, {"endpoint": "exa/search", "usd": float((data.get("costDollars") or {}).get("total") or 0)}


async def _brave(http: httpx.AsyncClient, query: str):
    r = await http.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": config.BRAVE_SEARCH_API_KEY, "Accept": "application/json"},
        params={"q": query, "count": MAX_RESULTS_PER_QUERY, "extra_snippets": "true"},
    )
    _check(r)
    items = [
        _item(i.get("title"), i.get("url"), " ".join([i.get("description") or ""] + (i.get("extra_snippets") or [])))
        for i in (r.json().get("web") or {}).get("results", [])
    ]
    return items, {"endpoint": "brave/web/search"}


async def _firecrawl(http: httpx.AsyncClient, query: str):
    r = await http.post(
        "https://api.firecrawl.dev/v2/search",
        headers={"Authorization": f"Bearer {config.FIRECRAWL_API_KEY}"},
        json={"query": query[:500], "limit": MAX_RESULTS_PER_QUERY, "timeout": QUERY_TIMEOUT * 1000},
    )
    _check(r)
    data = r.json()
    if data.get("success") is False:
        message = str(data.get("error") or "")
        if CREDIT_WORDS.search(message):
            raise OutOfCredits(message[:200])
        raise ValueError(message[:200] or "Firecrawl reported a failure")
    web = data.get("data") or {}
    web = web.get("web", []) if isinstance(web, dict) else web  # v2 nests results under data.web
    items = [_item(i.get("title"), i.get("url"), i.get("description") or i.get("markdown")) for i in web]
    return items, {"endpoint": "firecrawl/v2/search", "credits": data.get("creditsUsed")}


async def _linkup_request(http: httpx.AsyncClient, body: dict, timeout: int) -> dict:
    r = await http.post(
        "https://api.linkup.so/v1/search",
        headers={"Authorization": f"Bearer {config.LINKUP_API_KEY}"},
        json=body,
        timeout=timeout,
    )
    _check(r)  # Linkup uses 429 for both rate limits and "insufficient credits"; the body tells them apart
    return r.json()


async def _linkup(http: httpx.AsyncClient, query: str):
    data = await _linkup_request(
        http, {"q": query, "depth": "standard", "outputType": "searchResults", "maxResults": MAX_RESULTS_PER_QUERY}, QUERY_TIMEOUT
    )
    items = [_item(i.get("name"), i.get("url"), i.get("content")) for i in data.get("results", []) if i.get("type", "text") == "text"]
    return items, {"endpoint": "linkup/search standard"}


async def _linkup_deep(http: httpx.AsyncClient, query: str):
    """Deep research: Linkup searches several times and writes a sourced answer. The
    answer becomes one result (under its first source) and each source another."""
    data = await _linkup_request(http, {"q": query, "depth": "deep", "outputType": "sourcedAnswer"}, TIMEOUTS["linkup_deep"])
    answer = (data.get("answer") or "").strip()
    sources = data.get("sources") or []
    items = []
    if answer and sources:
        items.append({"title": f"Linkup answer: {query}", "url": sources[0].get("url"), "content": answer})
    items += [_item(src.get("name"), src.get("url"), src.get("snippet")) for src in sources]
    return items, {"endpoint": "linkup/search deep", "answer": answer}


CITATION = re.compile(r"<citation>(.*?)</citation>", re.S)
USAGE = re.compile(r"<usage>(.*?)</usage>", re.S)


async def _brave_answers(http: httpx.AsyncClient, query: str):
    """A written answer with citations. The answer becomes one result (under its
    first cited URL) and every citation becomes a result of its own."""
    text = ""
    async with http.stream(
        "POST",
        "https://api.search.brave.com/res/v1/chat/completions",
        headers={"x-subscription-token": config.BRAVE_ANSWERS_API_KEY},
        json={"model": "brave", "stream": True, "messages": [{"role": "user", "content": query}],
              "country": "us", "language": "en", "enable_citations": True},
    ) as r:
        if r.status_code >= 400:
            await r.aread()
            _check(r)
        async for line in r.aiter_lines():
            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                continue
            chunk = json.loads(line[5:])
            text += (chunk.get("choices") or [{}])[0].get("delta", {}).get("content") or ""

    citations = []
    for raw in CITATION.findall(text):
        try:
            citations.append(json.loads(raw))
        except ValueError:
            continue
    answer = _strip_tags(USAGE.sub("", CITATION.sub("", text))).strip()
    items = []
    if citations and answer:
        items.append({"title": f"Brave answer: {query}", "url": citations[0].get("url"), "content": answer})
    items += [_item(c.get("url"), c.get("url"), c.get("snippet")) for c in citations]
    return items, {"endpoint": "brave/chat/completions", "answer": answer}


def _strip_tags(text: str | None) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))  # Brave returns &quot; and <strong>


def as_prompt(results: list[dict]) -> str:
    blocks = [f"[{i}] {r['title']}\n{r['url']}\n{r['content']}" for i, r in enumerate(results, start=1)]
    return "<search_results>\n" + "\n\n".join(blocks) + "\n</search_results>"
