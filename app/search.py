"""Web search through Tavily, for free-mode deep dives: the Jetstream models can't
search on their own, so the server searches and hands them the results."""

import logging

import httpx

from . import config

log = logging.getLogger(__name__)

MAX_RESULTS_PER_QUERY = 4
MAX_RESULTS_TOTAL = 10
SNIPPET_CHARS = 1200


def enabled() -> bool:
    return bool(config.TAVILY_API_KEY)


async def search(queries: list[str]) -> list[dict]:
    """Run each query; return up to MAX_RESULTS_TOTAL unique results as
    {title, url, content}. Failed queries are skipped, not fatal."""
    results: list[dict] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=30) as http:
        for query in queries:
            try:
                response = await http.post(
                    "https://api.tavily.com/search",
                    headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
                    json={"query": query, "search_depth": config.TAVILY_SEARCH_DEPTH, "max_results": MAX_RESULTS_PER_QUERY},
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("tavily search failed for %r: %s", query, exc)
                continue
            for item in response.json().get("results", []):
                url = item.get("url")
                if url and url not in seen:
                    seen.add(url)
                    results.append({
                        "title": item.get("title", ""),
                        "url": url,
                        "content": (item.get("content") or "")[:SNIPPET_CHARS],
                    })
    return results[:MAX_RESULTS_TOTAL]


def as_prompt(results: list[dict]) -> str:
    blocks = [f"[{i}] {r['title']}\n{r['url']}\n{r['content']}" for i, r in enumerate(results, start=1)]
    return "<search_results>\n" + "\n\n".join(blocks) + "\n</search_results>"
