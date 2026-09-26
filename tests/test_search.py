"""Each research service's response is turned into {title, url, content} results."""

import asyncio
import json
import time

import httpx
import pytest

from app import config, search


@pytest.fixture
def keys(monkeypatch):
    for name in ("TAVILY_API_KEY", "EXA_API_KEY", "BRAVE_SEARCH_API_KEY", "BRAVE_ANSWERS_API_KEY", "FIRECRAWL_API_KEY", "LINKUP_API_KEY"):
        monkeypatch.setattr(config, name, "k-" + name)
    search._status.clear()  # each test starts with every service healthy
    monkeypatch.setattr(search, "_status_loaded", True)
    yield
    search._status.clear()


def mock(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_available_follows_keys(monkeypatch, keys):
    assert set(search.available()) == {"all", "tavily", "exa", "brave", "brave_answers", "firecrawl", "linkup", "linkup_deep"}
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
    assert r.results == [{"title": "T", "url": "https://a", "content": "one … two", "service": "exa"}] and r.usd == 0.005 and r.queries == 1


def test_brave_search(monkeypatch, keys):
    def handler(request):
        assert request.headers["x-subscription-token"] == "k-BRAVE_SEARCH_API_KEY"
        assert request.url.path == "/res/v1/web/search" and request.url.params["q"] == "q"
        return httpx.Response(200, json={"web": {"results": [
            {"title": "<strong>Luke</strong> 15", "url": "https://b", "description": "desc", "extra_snippets": ["more"]}]}})

    mock(monkeypatch, handler)
    r = asyncio.run(search.search(["q"], "brave"))
    assert r.results == [{"title": "Luke 15", "url": "https://b", "content": "desc more", "service": "brave"}]


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
    assert r.results[0] == {"title": "Brave answer: why did he run", "url": "https://c", "content": "The father ran.", "service": "brave_answers"}


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
    assert r.results == [{"title": "F", "url": "https://f", "content": "fd", "service": "firecrawl"}] and r.provider == "firecrawl"
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


def test_linkup_search_and_deep_research(monkeypatch, keys):
    def handler(request):
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer k-LINKUP_API_KEY" and request.url.path == "/v1/search"
        if body["depth"] == "deep":
            assert body["outputType"] == "sourcedAnswer"
            return httpx.Response(200, json={"answer": "The father ran.", "sources": [
                {"name": "Commentary", "url": "https://l/deep", "snippet": "ran to meet him"}]})
        return httpx.Response(200, json={"results": [{"type": "text", "name": "L", "url": "https://l/1", "content": "c"}]})

    mock(monkeypatch, handler)
    r = asyncio.run(search.search(["q"], "linkup"))
    assert r.results == [{"title": "L", "url": "https://l/1", "content": "c", "service": "linkup"}]
    r = asyncio.run(search.search(["q"], "linkup_deep"))
    assert r.results[0] == {"title": "Linkup answer: q", "url": "https://l/deep", "content": "The father ran.", "service": "linkup_deep"}


def test_linkup_out_of_credits_pauses_both_linkup_options(monkeypatch, keys):
    mock(monkeypatch, lambda request: httpx.Response(429, json={"error": "Insufficient credits"}) if request.url.host == "api.linkup.so" else brave_ok(request))
    r = asyncio.run(search.search(["q"], "linkup_deep"))
    assert r.provider == "brave"
    assert search.paused("linkup")["reason"] == "out of monthly credits" and search.paused("linkup_deep")


def test_all_services_combined(monkeypatch, keys):
    """"all" asks every service at once (not deep research) and interleaves the results."""
    def handler(request):
        host = request.url.host
        if "exa" in host:
            return httpx.Response(200, json={"results": [{"title": "E1", "url": "https://e/1", "highlights": ["e"]},
                                                         {"title": "E2", "url": "https://e/2", "highlights": ["e"]}]})
        if "tavily" in host:
            return httpx.Response(200, json={"results": [{"title": "T1", "url": "https://t/1", "content": "t"}]})
        return httpx.Response(500, json={})  # everyone else is down today

    mock(monkeypatch, handler)
    assert search.default_provider() == "all"
    r = asyncio.run(search.search(["q"], "all"))
    urls = [x["url"] for x in r.results]
    assert set(urls) == {"https://e/1", "https://e/2", "https://t/1"} and urls[-1] == "https://e/2"  # one of each first
    assert r.provider == "all" and set(r.contributors) == {"exa", "tavily"}
    assert not any("linkup/deep" in x["url"] for x in r.results)


def test_claude_gets_free_research_first(monkeypatch, keys):
    """Claude models: the free services search first; Claude still searches fully on its own."""
    from types import SimpleNamespace as NS

    from app import llm, pricing

    monkeypatch.setattr(config, "LLM_MODE", "openrouter")
    monkeypatch.setattr(config, "WEB_SEARCH", True)
    seen = []

    async def fake_call(meter, **params):
        seen.append(params)
        if params["max_tokens"] == 1000:
            return NS(content=[NS(type="text", text="setting of Luke 15\nGreek splanchnizomai\nfathers on the prodigal")])
        return NS(content=[
            NS(type="server_tool_use", input={"query": "Rembrandt prodigal son date"}),
            NS(type="web_search_tool_result", content=[NS(url="https://own/1", title="Own", page_age=None)]),
            NS(type="text", text="<script>Talk.</script><sources>\n- A https://free/1\n- Own https://own/1\n</sources>"),
        ])

    async def fake_search(queries, provider=None):
        r = search.Research()
        r.add("A", "https://free/1", "text", service="exa")
        r.provider, r.contributors = "all", ["exa"]
        return r

    monkeypatch.setattr(llm, "_call", fake_call)
    monkeypatch.setattr(search, "search", fake_search)
    meter = pricing.Meter("anthropic/claude-opus-5", {})
    script, sources, searched = asyncio.run(llm.write_deep("Day 5\nWelcomed Home\nLuke 15:17-24", "Write.", 300, meter, "all"))
    write = seen[-1]
    assert "https://free/1" in write["messages"][0]["content"] and write["tools"][0]["max_uses"] == 5
    assert "Don't let the results limit you" in write["system"]
    assert searched == "all" and len(sources) == 2
    assert meter.research["queries"][:3] == ["setting of Luke 15", "Greek splanchnizomai", "fathers on the prodigal"]
    assert [x["url"] for x in meter.research["results"]] == ["https://free/1", "https://own/1"]


def test_claude_citations_become_research_results():
    from types import SimpleNamespace as NS

    from app import llm

    blocks = [NS(type="server_tool_use", input={"query": "splanchnizomai"}),
              NS(type="text", text="x", citations=[NS(url="https://bh/4697", title="Strong's 4697", cited_text="to be moved"),
                                                  NS(url="https://bh/4697", title="Strong's 4697", cited_text="compassion")])]
    log = llm._web_search_log(blocks)
    assert log["queries"] == ["splanchnizomai"] and len(log["results"]) == 1
    assert "to be moved" in log["results"][0]["content"] and "compassion" in log["results"][0]["content"]
