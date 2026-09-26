"""Each research service's response is turned into {title, url, content} results."""

import asyncio
import json
import time

import httpx
import pytest

from app import config, search


@pytest.fixture
def keys(monkeypatch):
    for name in ("TAVILY_API_KEY", "EXA_API_KEY", "BRAVE_SEARCH_API_KEY", "BRAVE_ANSWERS_API_KEY", "FIRECRAWL_API_KEY"):
        monkeypatch.setattr(config, name, "k-" + name)
    search._status.clear()  # each test starts with every service healthy
    monkeypatch.setattr(search, "_status_loaded", True)
    yield
    search._status.clear()


def mock(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_available_follows_keys(monkeypatch, keys):
    assert set(search.available()) == {"tavily", "exa", "brave", "brave_answers", "firecrawl"}
    monkeypatch.setattr(config, "EXA_API_KEY", "")
    assert "exa" not in search.available()


def test_exa(monkeypatch, keys):
    def handler(request):
        assert request.headers["x-api-key"] == "k-EXA_API_KEY" and request.url.path == "/search"
        assert json.loads(request.content)["contents"]["highlights"]
        return httpx.Response(200, json={"results": [{"title": "T", "url": "https://a", "highlights": ["one", "two"]}],
                                         "costDollars": {"total": 0.005}})

    mock(monkeypatch, handler)
    r = asyncio.run(search.search(["q"], "exa"))
    assert r.results == [{"title": "T", "url": "https://a", "content": "one … two"}] and r.usd == 0.005 and r.queries == 1


def test_brave_search(monkeypatch, keys):
    def handler(request):
        assert request.headers["x-subscription-token"] == "k-BRAVE_SEARCH_API_KEY"
        assert request.url.path == "/res/v1/web/search" and request.url.params["q"] == "q"
        return httpx.Response(200, json={"web": {"results": [
            {"title": "<strong>Luke</strong> 15", "url": "https://b", "description": "desc", "extra_snippets": ["more"]}]}})

    mock(monkeypatch, handler)
    r = asyncio.run(search.search(["q"], "brave"))
    assert r.results == [{"title": "Luke 15", "url": "https://b", "content": "desc more"}]


def test_brave_answers_parses_stream_and_citations(monkeypatch, keys):
    cite = json.dumps({"number": 1, "url": "https://c", "snippet": "the father ran"})
    chunks = ["The father ", f"ran.<citation>{cite}</citation>", '<usage>{"X-Request-Queries": 2}</usage>']
    body = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def handler(request):
        sent = json.loads(request.content)
        assert request.headers["x-subscription-token"] == "k-BRAVE_ANSWERS_API_KEY"
        assert sent["model"] == "brave" and sent["enable_citations"] and sent["stream"]
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    mock(monkeypatch, handler)
    r = asyncio.run(search.search(["why did he run"], "brave_answers"))
    assert r.results[0] == {"title": "Brave answer: why did he run", "url": "https://c", "content": "The father ran."}


def log_rows():
    from app.storage import store

    path = store.rows.parent / "llm_calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def brave_ok(request):
    return httpx.Response(200, json={"web": {"results": [{"title": "B", "url": "https://brave.example", "description": "d"}]}})


def test_firecrawl(monkeypatch, keys):
    def handler(request):
        assert request.headers["authorization"] == "Bearer k-FIRECRAWL_API_KEY" and request.url.path == "/v2/search"
        return httpx.Response(200, json={"success": True, "creditsUsed": 2,
                                         "data": {"web": [{"title": "F", "url": "https://f", "description": "fd"}]}})

    mock(monkeypatch, handler)
    before = len(log_rows())
    r = asyncio.run(search.search(["q"], "firecrawl"))
    assert r.results == [{"title": "F", "url": "https://f", "content": "fd"}] and r.provider == "firecrawl"
    row = log_rows()[before]
    assert row["provider"] == "firecrawl" and row["purpose"] in ("research", "unknown") and row["status"] == "ok"
    assert row["response"]["results"][0]["url"] == "https://f" and row["response"]["credits"] == 2


def test_out_of_credits_pauses_until_next_month_and_falls_back(monkeypatch, keys):
    def handler(request):
        if request.url.host == "api.exa.ai":
            return httpx.Response(402, json={"error": "Insufficient credits"})
        return brave_ok(request)

    mock(monkeypatch, handler)
    before = len(log_rows())
    r = asyncio.run(search.search(["q1", "q2"], "exa"))
    assert r.provider == "brave" and r.results[0]["url"] == "https://brave.example"
    assert search.paused("exa")["reason"] == "out of monthly credits"
    assert search.paused("exa")["until"] == search._next_month()
    assert "exa" not in search.available() and search.status()["exa"]["paused"]
    rows = log_rows()[before:]
    assert rows[0]["provider"] == "exa" and rows[0]["status"] == "error" and "credits" in rows[0]["error"]
    assert [row["provider"] for row in rows[1:]] == ["brave", "brave"]  # exa stopped after one failed query


def test_tavily_plan_limit_code_counts_as_out_of_credits(monkeypatch, keys):
    mock(monkeypatch, lambda request: httpx.Response(432, json={"detail": {"error": "This request exceeds your plan's set usage limit."}}) if request.url.host == "api.tavily.com" else brave_ok(request))
    asyncio.run(search.search(["q"], "tavily"))
    assert search.paused("tavily")["reason"] == "out of monthly credits"


def test_pause_survives_a_restart(monkeypatch, keys):
    mock(monkeypatch, lambda request: httpx.Response(402) if request.url.host == "api.exa.ai" else brave_ok(request))
    asyncio.run(search.search(["q"], "exa"))
    search._status.clear()
    monkeypatch.setattr(search, "_status_loaded", False)  # as if the server restarted
    asyncio.run(search._load_status())
    assert search.paused("exa")


def test_rate_limit_pauses_briefly(monkeypatch, keys):
    mock(monkeypatch, lambda request: httpx.Response(429, text="Too many requests") if request.url.host == "api.search.brave.com" else httpx.Response(500))
    asyncio.run(search.search(["q"], "brave"))
    p = search.paused("brave")
    assert p["reason"] == "rate limited" and p["until"] - time.time() < 120


def test_everything_failing_never_raises(monkeypatch, keys):
    def handler(request):
        if request.url.host == "api.exa.ai":
            raise httpx.ConnectTimeout("slow")
        if request.url.host == "api.firecrawl.dev":
            return httpx.Response(200, text="not json")
        return httpx.Response(500)

    mock(monkeypatch, handler)
    for _ in range(3):
        r = asyncio.run(search.search(["q"], "exa"))
        assert r.results == [] and r.provider is None
    assert search.paused("exa")["reason"] == "failed three times in a row"


def test_failed_query_is_skipped(monkeypatch, keys):
    mock(monkeypatch, lambda request: httpx.Response(500))
    r = asyncio.run(search.search(["q1", "q2"], "tavily"))
    assert r.results == [] and r.queries == 0
