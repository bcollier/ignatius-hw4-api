"""Every model call is logged with who made it and why."""

import asyncio
import json
from types import SimpleNamespace

import httpx

from app import config, jetstream, llm, llm_log, pricing
from app.storage import store


def log_rows():
    path = store.rows.parent / "llm_calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_jetstream_call_is_logged_with_context(monkeypatch):
    monkeypatch.setattr(config, "JETSTREAM_API_KEY", "t")

    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "<script>Hi.</script>", "reasoning_content": "thinking…"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 9},
        })

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    async def run():
        llm_log.tag(user_id="u-1", email="guest", retreat_id="r-1", day=2, purpose="heart")
        return await jetstream.complete("muse-glimmer", "SYSTEM", "USER TEXT", pricing.Meter("jetstream/muse-glimmer", {}),
                                        images=[(b"\xff" * 3000, "image/jpeg")])

    before = len(log_rows())
    asyncio.run(run())
    row = log_rows()[before]
    assert (row["user_id"], row["email"], row["retreat_id"], row["day"], row["purpose"]) == ("u-1", "guest", "r-1", 2, "heart")
    assert row["provider"] == "jetstream" and row["model"] == "jetstream/muse-glimmer" and row["status"] == "ok"
    assert row["request"]["system"].startswith("Background for this work") and row["request"]["system"].endswith("SYSTEM")
    assert row["request"]["messages"][0]["content"][1] == {"type": "image", "media_type": "image/jpeg", "bytes": 3000}
    assert row["response_text"] == "<script>Hi.</script>" and row["response"]["reasoning"] == "thinking…"
    assert (row["input_tokens"], row["output_tokens"]) == (40, 9) and row["duration_ms"] >= 0


def test_claude_call_and_failure_are_logged(monkeypatch):
    usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
                            cache_creation_input_tokens=0, server_tool_use=SimpleNamespace(web_search_requests=1))
    message = SimpleNamespace(
        stop_reason="end_turn", usage=usage,
        content=[SimpleNamespace(type="server_tool_use", input={"query": "Luke 15 setting"}),
                 SimpleNamespace(type="text", text="<script>Deep.</script>")],
    )

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_final_message(self):
            return message

    fail = {"on": False}

    class FakeClient:
        messages = SimpleNamespace(stream=lambda **kw: (_ for _ in ()).throw(llm.LLMError("boom")) if fail["on"] else Stream())

    monkeypatch.setattr(llm, "client", lambda: FakeClient())
    meter = pricing.Meter("anthropic/claude-opus-5", pricing.FALLBACK_PRICES)

    async def run():
        llm_log.tag(user_id="u-2", purpose="deep")
        await llm._call(meter, system="S", max_tokens=100, messages=[{"role": "user", "content": "C"}],
                        tools=[{"type": "web_search_20260209", "name": "web_search"}])

    before = len(log_rows())
    asyncio.run(run())
    row = log_rows()[before]
    assert row["purpose"] == "deep" and row["response_text"] == "<script>Deep.</script>"
    assert row["response"]["web_search_queries"] == [{"query": "Luke 15 setting"}]
    assert row["web_searches"] == 1 and row["usd"] > 0 and row["status"] == "ok"
    assert "Lectio divina" in row["request"]["system"] and row["request"]["system"].endswith("S")

    fail["on"] = True
    try:
        asyncio.run(run())
    except llm.LLMError:
        pass
    row = log_rows()[-1]
    assert row["status"] == "error" and row["error"] == "boom" and row["response_text"] is None
