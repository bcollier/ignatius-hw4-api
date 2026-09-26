"""Each research service's response is turned into {title, url, content} results."""

import asyncio
import json

import httpx
import pytest

from app import config, search


@pytest.fixture
def keys(monkeypatch):
    for name in ("TAVILY_API_KEY", "EXA_API_KEY", "BRAVE_SEARCH_API_KEY", "BRAVE_ANSWERS_API_KEY"):
        monkeypatch.setattr(config, name, "k-" + name)


def mock(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_available_follows_keys(monkeypatch, keys):
    assert set(search.available()) == {"tavily", "exa", "brave", "brave_answers"}
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


def test_failed_query_is_skipped(monkeypatch, keys):
    mock(monkeypatch, lambda request: httpx.Response(500))
    r = asyncio.run(search.search(["q1", "q2"], "tavily"))
    assert r.results == [] and r.queries == 0
