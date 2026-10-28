import json

import httpx
import pytest

from billbrake.config import from_dict
from billbrake.pricing import DEFAULT_PRICING, cost_usd
from billbrake.proxy import create_app


def make(tmp_path, handler, **proj):
    p = {"keys": ["bb-test"], "daily_usd": 1.0}
    p.update(proj)
    cfg = from_dict({"db_path": str(tmp_path / "t.db"), "projects": {"demo": p}})
    app = create_app(cfg, transport=httpx.MockTransport(handler))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bb"), app


def openai_ok(prompt=1000, completion=1000):
    def h(req):
        assert req.headers["authorization"] == "Bearer sk-real"
        return httpx.Response(200, json={"model": "gpt-4o", "choices": [],
                                         "usage": {"prompt_tokens": prompt, "completion_tokens": completion}})
    return h


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-real")


def test_prefix_pricing():
    assert cost_usd("claude-sonnet-4-20250514", 1_000_000, 0, DEFAULT_PRICING) == 3.0


async def test_rejects_unknown_key(tmp_path):
    c, _ = make(tmp_path, openai_ok())
    r = await c.post("/v1/chat/completions", json={"model": "gpt-4o"}, headers={"authorization": "Bearer nope"})
    assert r.status_code == 401


async def test_records_spend_and_blocks_at_cap(tmp_path):
    calls = []
    inner = openai_ok(prompt=100_000, completion=50_000)  # 0.25 + 0.50 = $0.75

    def h(req):
        calls.append(req)
        return inner(req)
    c, _ = make(tmp_path, h)
    hdr = {"authorization": "Bearer bb-test"}
    r1 = await c.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": []}, headers=hdr)
    assert r1.status_code == 200
    r2 = await c.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": []}, headers=hdr)
    assert r2.status_code == 200  # 0.75 < 1.0 before; now 1.50
    r3 = await c.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": []}, headers=hdr)
    assert r3.status_code == 429
    assert r3.json()["error"]["type"] == "spend_cap_exceeded"
    assert len(calls) == 2  # blocked request never reached the provider
    st = (await c.get("/billbrake/status")).json()["demo"]
    assert st["today_usd"] == pytest.approx(1.5) and st["blocked"]


async def test_reserve_blocks_before_overshoot(tmp_path):
    c, _ = make(tmp_path, openai_ok(prompt=100_000, completion=50_000), reserve_usd=0.5)
    hdr = {"authorization": "Bearer bb-test"}
    assert (await c.post("/v1/chat/completions", json={"model": "gpt-4o"}, headers=hdr)).status_code == 200
    # spent 0.75; 0.75 + 0.5 reserve >= 1.0 -> blocked
    assert (await c.post("/v1/chat/completions", json={"model": "gpt-4o"}, headers=hdr)).status_code == 429


async def test_unknown_model_blocked(tmp_path):
    c, _ = make(tmp_path, openai_ok())
    r = await c.post("/v1/chat/completions", json={"model": "mystery-9"}, headers={"authorization": "Bearer bb-test"})
    assert r.status_code == 400


async def test_anthropic_nonstream(tmp_path):
    def h(req):
        assert req.url.path == "/v1/messages"
        assert req.headers["x-api-key"] == "ak-real"
        return httpx.Response(200, json={"usage": {"input_tokens": 1_000_000, "output_tokens": 0}})
    c, _app = make(tmp_path, h, daily_usd=10)
    r = await c.post("/v1/messages", json={"model": "claude-sonnet-4-20250514"}, headers={"x-api-key": "bb-test"})
    assert r.status_code == 200
    assert (await c.get("/billbrake/status")).json()["demo"]["today_usd"] == pytest.approx(3.0)


async def test_openai_stream_injects_usage_and_counts(tmp_path):
    seen = {}

    def h(req):
        seen["body"] = json.loads(req.content)
        sse = (b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
               b'data: {"choices":[],"usage":{"prompt_tokens":400000,"completion_tokens":0}}\n\n'
               b'data: [DONE]\n\n')
        return httpx.Response(200, stream=httpx.ByteStream(sse), headers={"content-type": "text/event-stream"})
    c, _ = make(tmp_path, h)
    r = await c.post("/v1/chat/completions", json={"model": "gpt-4o", "stream": True},
                     headers={"authorization": "Bearer bb-test"})
    assert r.status_code == 200 and b"[DONE]" in r.content
    assert seen["body"]["stream_options"]["include_usage"] is True
    assert (await c.get("/billbrake/status")).json()["demo"]["today_usd"] == pytest.approx(1.0)


async def test_anthropic_stream_counts(tmp_path):
    def h(req):
        sse = (b'event: message_start\ndata: {"type":"message_start","message":{"usage":'
               b'{"input_tokens":1000000,"output_tokens":1}}}\n\n'
               b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":100000}}\n\n')
        return httpx.Response(200, stream=httpx.ByteStream(sse))
    c, _ = make(tmp_path, h, daily_usd=100)
    r = await c.post("/v1/messages", json={"model": "claude-sonnet-4", "stream": True}, headers={"x-api-key": "bb-test"})
    assert r.status_code == 200
    assert (await c.get("/billbrake/status")).json()["demo"]["today_usd"] == pytest.approx(4.5)


async def test_admin_token(tmp_path):
    cfg = from_dict({"db_path": str(tmp_path / "a.db"), "admin_token": "s3cret",
                     "projects": {"d": {"keys": ["k"], "daily_usd": 1}}})
    app = create_app(cfg, transport=httpx.MockTransport(openai_ok()))
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bb")
    assert (await c.get("/billbrake/status")).status_code == 401
    assert (await c.get("/billbrake/status", headers={"authorization": "Bearer s3cret"})).status_code == 200


async def test_upstream_error_releases_reserve(tmp_path):
    def h(req):
        raise httpx.ConnectError("down")
    c, _ = make(tmp_path, h, reserve_usd=0.4)
    r = await c.post("/v1/chat/completions", json={"model": "gpt-4o"}, headers={"authorization": "Bearer bb-test"})
    assert r.status_code == 502
    assert (await c.get("/billbrake/status")).json()["demo"]["today_usd"] == pytest.approx(0.0)
