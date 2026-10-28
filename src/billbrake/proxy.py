"""Spend-capping reverse proxy for OpenAI-compatible and Anthropic APIs."""
from __future__ import annotations

import json
import os

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .config import Config, Project
from .pricing import UnknownModel, cost_usd
from .store import Store, periods

HOP_HEADERS = {"host", "content-length", "authorization", "x-api-key", "connection", "accept-encoding"}


def _client_key(req: Request) -> str | None:
    auth = req.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return req.headers.get("x-api-key")


def _err(status: int, msg: str, kind: str = "billbrake_error") -> JSONResponse:
    return JSONResponse({"error": {"type": kind, "message": msg}}, status_code=status)


def over_cap(store: Store, p: Project, extra: float = 0.0) -> str | None:
    day, month = periods()
    if p.daily_usd is not None and store.get(p.name, day) + extra >= p.daily_usd:
        return f"daily cap ${p.daily_usd:.2f} reached for project '{p.name}'"
    if p.monthly_usd is not None and store.get(p.name, month) + extra >= p.monthly_usd:
        return f"monthly cap ${p.monthly_usd:.2f} reached for project '{p.name}'"
    return None


def _usage(provider: str, data: dict) -> tuple[int, int] | None:
    u = data.get("usage")
    if not isinstance(u, dict):
        return None
    if provider == "anthropic":
        i = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) \
            + (u.get("cache_read_input_tokens") or 0)
        return i, u.get("output_tokens") or 0
    return u.get("prompt_tokens") or u.get("input_tokens") or 0, \
        u.get("completion_tokens") or u.get("output_tokens") or 0


def create_app(cfg: Config, transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    store = Store(cfg.db_path)
    client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(600, connect=10))

    def settle(p: Project, provider: str, model: str, itok: int, otok: int, reserved: float, status: str):
        try:
            usd = cost_usd(model, itok, otok, cfg.pricing)
        except UnknownModel:
            usd = 0.0
        store.add(p.name, usd - reserved)
        store.log(p.name, provider, model, itok, otok, usd, status)

    async def forward(req: Request, provider: str, upstream_path: str) -> Response:
        p = cfg.project_for_key(_client_key(req))
        if p is None:
            return _err(401, "unknown billbrake key", "authentication_error")
        body = await req.body()
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return _err(400, "body must be JSON")
        model = str(payload.get("model", ""))
        if cfg.block_unknown_models:
            try:
                cost_usd(model, 0, 0, cfg.pricing)
            except UnknownModel:
                return _err(400, f"model '{model}' has no price in billbrake config; add it under pricing:")
        reason = over_cap(store, p, p.reserve_usd)
        if reason:
            store.log(p.name, provider, model, 0, 0, 0.0, "blocked")
            return _err(429, reason, "spend_cap_exceeded")
        stream = bool(payload.get("stream"))
        if stream and provider == "openai" and upstream_path.endswith("/chat/completions"):
            payload.setdefault("stream_options", {})["include_usage"] = True
            body = json.dumps(payload).encode()

        headers = {k: v for k, v in req.headers.items() if k.lower() not in HOP_HEADERS}
        if provider == "openai":
            headers["authorization"] = f"Bearer {os.environ.get(cfg.openai_key_env, '')}"
            base = cfg.openai_base
        else:
            headers["x-api-key"] = os.environ.get(cfg.anthropic_key_env, "")
            headers.setdefault("anthropic-version", "2023-06-01")
            base = cfg.anthropic_base
        if p.reserve_usd:
            store.add(p.name, p.reserve_usd, count=False)
        reserved = p.reserve_usd
        upstream = client.build_request(req.method, base + upstream_path, content=body, headers=headers,
                                        params=req.query_params)
        try:
            resp = await client.send(upstream, stream=stream)
        except httpx.HTTPError as e:
            store.add(p.name, -reserved, count=False)
            return _err(502, f"upstream error: {e}")

        out_headers = {k: v for k, v in resp.headers.items()
                       if k.lower() not in {"content-length", "content-encoding", "transfer-encoding", "connection"}}
        if not stream:
            content = resp.content
            itok = otok = 0
            try:
                u = _usage(provider, resp.json())
                if u:
                    itok, otok = u
            except ValueError:
                pass
            settle(p, provider, model, itok, otok, reserved, str(resp.status_code))
            return Response(content, status_code=resp.status_code, headers=out_headers)

        async def gen():
            itok = otok = 0
            buf = b""
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if not line.startswith(b"data:"):
                            continue
                        data = line[5:].strip()
                        if data == b"[DONE]":
                            continue
                        try:
                            ev = json.loads(data)
                        except ValueError:
                            continue
                        if provider == "anthropic":
                            if ev.get("type") == "message_start":
                                u = _usage("anthropic", ev.get("message", {}))
                                if u:
                                    itok, otok = u[0], max(otok, u[1])
                            elif ev.get("type") == "message_delta":
                                otok = (ev.get("usage") or {}).get("output_tokens", otok)
                        else:
                            u = _usage("openai", ev)
                            if u:
                                itok, otok = u
            finally:
                await resp.aclose()
                settle(p, provider, model, itok, otok, reserved, f"{resp.status_code}/stream")

        return StreamingResponse(gen(), status_code=resp.status_code, headers=out_headers)

    async def openai_route(req: Request):
        return await forward(req, "openai", "/v1/" + req.path_params["path"])

    async def anthropic_route(req: Request):
        return await forward(req, "anthropic", "/v1/messages")

    async def status(req: Request):
        if cfg.admin_token and req.headers.get("authorization") != f"Bearer {cfg.admin_token}":
            return _err(401, "admin token required", "authentication_error")
        return JSONResponse(status_snapshot(cfg, store))

    async def health(req: Request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[
        Route("/healthz", health),
        Route("/billbrake/status", status),
        Route("/v1/messages", anthropic_route, methods=["POST"]),
        Route("/anthropic/v1/messages", anthropic_route, methods=["POST"]),
        Route("/v1/{path:path}", openai_route, methods=["POST", "GET"]),
    ])
    app.state.store = store
    return app


def status_snapshot(cfg: Config, store: Store) -> dict:
    day, month = periods()
    out = {}
    for p in cfg.projects:
        d, m = store.get(p.name, day), store.get(p.name, month)
        out[p.name] = {
            "today_usd": round(d, 6), "daily_cap_usd": p.daily_usd,
            "month_usd": round(m, 6), "monthly_cap_usd": p.monthly_usd,
            "blocked": over_cap(store, p) is not None,
        }
    return out
