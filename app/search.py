"""Web research for free-mode deep dives. The Jetstream models can't search, so the
server runs the queries through one of several services and hands the model the
results, each as {title, url, content}. The model may cite only these URLs.

Providers (each on when its key is set):
  tavily         Tavily search                         TAVILY_API_KEY
  exa            Exa search, with page highlights      EXA_API_KEY
  brave          Brave Search API, web results         BRAVE_SEARCH_API_KEY
  brave_answers  Brave Answers: a cited written answer BRAVE_ANSWERS_API_KEY
"""

import json
import logging
import re

import httpx

from . import config, llm_log

log = logging.getLogger(__name__)

MAX_RESULTS_PER_QUERY = 4
MAX_RESULTS_TOTAL = 12
SNIPPET_CHARS = 1200

PROVIDERS = {
    "tavily": "Tavily",
    "exa": "Exa",
    "brave": "Brave Search",
    "brave_answers": "Brave Answers (cited summaries)",
}


def available() -> dict[str, str]:
    keys = {
        "tavily": config.TAVILY_API_KEY,
        "exa": config.EXA_API_KEY,
        "brave": config.BRAVE_SEARCH_API_KEY,
        "brave_answers": config.BRAVE_ANSWERS_API_KEY,
    }
    return {k: label for k, label in PROVIDERS.items() if keys[k]}


def default_provider() -> str | None:
    wanted = config.SEARCH_PROVIDER
    options = available()
    return wanted if wanted in options else next(iter(options), None)


def enabled() -> bool:
    return bool(available())


class Research:
    """What a search run found, plus what it cost (when the service reports it)."""

    def __init__(self):
        self.results: list[dict] = []
        self.queries = 0
        self.usd = 0.0

    def add(self, title: str, url: str, content: str) -> None:
        if url and url not in {r["url"] for r in self.results} and len(self.results) < MAX_RESULTS_TOTAL:
            self.results.append({"title": title or url, "url": url, "content": (content or "")[:SNIPPET_CHARS]})


async def search(queries: list[str], provider: str | None = None) -> Research:
    """Run each query with the provider. A failed query is skipped, not fatal."""
    provider = provider or default_provider()
    research = Research()
    if provider not in available():
        return research
    run = {"tavily": _tavily, "exa": _exa, "brave": _brave, "brave_answers": _brave_answers}[provider]
    async with httpx.AsyncClient(timeout=60) as http:
        for query in queries:
            try:
                await run(http, query, research)
                research.queries += 1
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                log.warning("%s search failed for %r: %s", provider, query, exc)
    return research


async def _tavily(http: httpx.AsyncClient, query: str, research: Research) -> None:
    r = await http.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
        json={"query": query, "search_depth": config.TAVILY_SEARCH_DEPTH, "max_results": MAX_RESULTS_PER_QUERY},
    )
    r.raise_for_status()
    for item in r.json().get("results", []):
        research.add(item.get("title"), item.get("url"), item.get("content"))


async def _exa(http: httpx.AsyncClient, query: str, research: Research) -> None:
    r = await http.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": config.EXA_API_KEY},
        json={
            "query": query,
            "type": "auto",
            "numResults": MAX_RESULTS_PER_QUERY,
            "contents": {"highlights": {"maxCharacters": SNIPPET_CHARS}},
        },
    )
    r.raise_for_status()
    data = r.json()
    for item in data.get("results", []):
        content = " … ".join(item.get("highlights") or []) or item.get("summary") or item.get("text") or ""
        research.add(item.get("title"), item.get("url"), content)
    research.usd += float((data.get("costDollars") or {}).get("total") or 0)


async def _brave(http: httpx.AsyncClient, query: str, research: Research) -> None:
    r = await http.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"X-Subscription-Token": config.BRAVE_SEARCH_API_KEY, "Accept": "application/json"},
        params={"q": query, "count": MAX_RESULTS_PER_QUERY, "extra_snippets": "true"},
    )
    r.raise_for_status()
    for item in (r.json().get("web") or {}).get("results", []):
        content = " ".join([item.get("description") or ""] + (item.get("extra_snippets") or []))
        research.add(_strip_tags(item.get("title")), item.get("url"), _strip_tags(content))


CITATION = re.compile(r"<citation>(.*?)</citation>", re.S)
USAGE = re.compile(r"<usage>(.*?)</usage>", re.S)


async def _brave_answers(http: httpx.AsyncClient, query: str, research: Research) -> None:
    """A written answer with citations. The answer becomes one result (under its
    first cited URL) and every citation becomes a result of its own. Logged as a
    model call, since Brave writes the answer."""
    timer = llm_log.Timer()
    text, error = "", None
    try:
        async with http.stream(
            "POST",
            "https://api.search.brave.com/res/v1/chat/completions",
            headers={"x-subscription-token": config.BRAVE_ANSWERS_API_KEY},
            json={
                "model": "brave",
                "stream": True,
                "messages": [{"role": "user", "content": query}],
                "country": "us",
                "language": "en",
                "enable_citations": True,
            },
        ) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:") or line.strip() == "data: [DONE]":
                    continue
                chunk = json.loads(line[5:])
                text += (chunk.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        await llm_log.record(
            provider="brave_answers", model="brave", system="", messages=[{"role": "user", "content": query}],
            response_text=text or None, response_extra={}, usage={}, duration_ms=timer.ms, error=error,
        )

    citations = []
    for raw in CITATION.findall(text):
        try:
            citations.append(json.loads(raw))
        except ValueError:
            continue
    answer = _strip_tags(USAGE.sub("", CITATION.sub("", text))).strip()
    if citations and answer:
        research.add(f"Brave answer: {query}", citations[0].get("url"), answer)
    for c in citations:
        research.add(c.get("url"), c.get("url"), c.get("snippet") or "")


def _strip_tags(text: str | None) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def as_prompt(results: list[dict]) -> str:
    blocks = [f"[{i}] {r['title']}\n{r['url']}\n{r['content']}" for i, r in enumerate(results, start=1)]
    return "<search_results>\n" + "\n\n".join(blocks) + "\n</search_results>"
