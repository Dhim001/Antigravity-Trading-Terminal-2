"""Starlette HTTP API — auto-routed from HTTP_BINDINGS."""

from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from app.api.http.bindings import HTTP_BINDINGS
from app.api.http.dispatch import http_status_and_body, invoke_action
from app.api.openapi import build_openapi_spec
from app.api.router import ensure_routes_loaded, list_routes
from app.api.state import AppState
from app.config import HTTP_API_KEY, HTTP_CORS_ORIGINS, HTTP_HOST, HTTP_PORT, REDIS_URL, TERMINAL_MODE, TERMINAL_ROLE, WS_HOST, WS_PORT
from app.api.http.auth import ApiKeyMiddleware
from app.database import get_db_stats
from app.services.bots.strategy_catalog import list_strategy_catalog
from app.services.bots.backtest_store import list_backtest_runs
from app.services.events import channels

ensure_routes_loaded()


async def metrics(request: Request) -> PlainTextResponse:
    from app.observability.metrics import render_prometheus

    return PlainTextResponse(render_prometheus(), media_type="text/plain; version=0.0.4")


async def health(request: Request) -> JSONResponse:
    import time

    state: AppState = request.app.state.terminal
    body = {
        "ok": True,
        "service": "trading-terminal",
        "terminal_mode": TERMINAL_MODE,
        "terminal_role": TERMINAL_ROLE,
        "ws_clients": len(state.manager.connected_clients),
        "websocket": f"ws://{WS_HOST}:{WS_PORT}",
        "http": f"http://{HTTP_HOST}:{HTTP_PORT}",
    }

    try:
        stats = get_db_stats()
        body["metrics"] = {
            "open_positions": stats.get("positions_count", 0),
            "pending_orders": stats.get("pending_orders_count", 0),
            "ambiguous_orders": (stats.get("reconciliation") or {}).get("pending_count", 0),
        }
    except Exception:
        pass

    if REDIS_URL and TERMINAL_ROLE == "server":
        try:
            import redis

            client = redis.from_url(REDIS_URL)
            raw = client.get(channels.WORKER_HEARTBEAT_KEY)
            if raw:
                age = time.time() - float(raw)
                body["worker"] = {"alive": age < 35, "heartbeat_age_sec": round(age, 1)}
            else:
                body["worker"] = {"alive": False, "heartbeat_age_sec": None}
        except Exception as exc:
            body["worker"] = {"alive": False, "error": str(exc)}

    return JSONResponse(body)


async def list_strategies(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "strategies": list_strategy_catalog()})


async def list_agent_insights(request: Request) -> JSONResponse:
    symbol = (request.path_params.get("symbol") or "").upper()
    if not symbol:
        return JSONResponse({"ok": False, "error": "symbol is required"}, status_code=400)
    try:
        limit = int(request.query_params.get("limit", "20"))
    except (TypeError, ValueError):
        limit = 20
    state: AppState = request.app.state.terminal
    analyst = state.chart_analyst
    if analyst is None:
        return JSONResponse({"ok": False, "error": "Chart analyst unavailable"}, status_code=503)
    insights = analyst.list_insights(symbol, limit=limit)
    return JSONResponse({"ok": True, "symbol": symbol, "insights": insights, "count": len(insights)})


async def list_backtest_runs_handler(request: Request) -> JSONResponse:
    symbol = request.query_params.get("symbol")
    try:
        limit = int(request.query_params.get("limit", "50"))
    except (TypeError, ValueError):
        limit = 50
    runs = list_backtest_runs(limit=limit, symbol=symbol or None)
    return JSONResponse({"ok": True, "runs": runs, "count": len(runs)})


async def list_api_routes(request: Request) -> JSONResponse:
    routes = list_routes()
    items = [
        {
            "action": action,
            "tags": meta.tags,
            "sim_only": meta.sim_only,
        }
        for action, meta in sorted(routes.items())
    ]
    return JSONResponse({"ok": True, "routes": items, "count": len(items)})


async def openapi_json(request: Request) -> JSONResponse:
    return JSONResponse(build_openapi_spec())


def _client_rate_key(request: Request) -> str:
    client = request.client
    return f"http:{client.host}:{client.port}" if client else "http:unknown"


async def _binding_handler(request: Request) -> JSONResponse:
    binding = request.scope["http_binding"]
    method, _path, action, param_map = binding
    state: AppState = request.app.state.terminal

    message: dict = {"_rate_key": _client_rate_key(request)}

    if param_map:
        for ws_field, path_field in param_map.items():
            if path_field in request.path_params:
                message[ws_field] = request.path_params[path_field]

    if method == "GET":
        for key, value in request.query_params.multi_items():
            if key not in message:
                message[key] = value

    if method in ("POST", "PATCH", "DELETE"):
        if method != "DELETE":
            body = {}
            if request.headers.get("content-length", "0") != "0":
                try:
                    parsed = await request.json()
                except json.JSONDecodeError:
                    return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)
                if isinstance(parsed, dict):
                    body = parsed
            message.update(body)

    if action == "place_order" and "type" not in message and "order_type" in message:
        message["type"] = message["order_type"]
    elif action == "place_order" and "type" not in message:
        message["type"] = "market"

    if action == "bot_create":
        if not message.get("strategy") or not message.get("symbol"):
            return JSONResponse(
                {"ok": False, "error": "strategy and symbol are required"},
                status_code=400,
            )
    if action == "place_order":
        missing = [f for f in ("symbol", "side", "quantity") if message.get(f) is None]
        if missing:
            return JSONResponse(
                {"ok": False, "error": f"Missing required fields: {', '.join(missing)}"},
                status_code=400,
            )

    messages = await invoke_action(state, action, message)
    status, body = http_status_and_body(messages)
    return JSONResponse(body, status_code=status)


def _make_endpoint(binding):
    async def endpoint(request: Request):
        request.scope["http_binding"] = binding
        return await _binding_handler(request)
    return endpoint


def create_http_app(state: AppState) -> Starlette:
    starlette_routes = [
        Route("/health", health, methods=["GET"]),
        Route("/metrics", metrics, methods=["GET"]),
        Route("/api/v1/strategies", list_strategies, methods=["GET"]),
        Route("/api/v1/agent/insights/{symbol}", list_agent_insights, methods=["GET"]),
        Route("/api/v1/backtest/runs", list_backtest_runs_handler, methods=["GET"]),
        Route("/api/v1/routes", list_api_routes, methods=["GET"]),
        Route("/api/v1/openapi.json", openapi_json, methods=["GET"]),
    ]

    seen: set[tuple[str, str]] = set()
    for method, path, action, param_map in HTTP_BINDINGS:
        key = (method, path)
        if key in seen:
            continue
        seen.add(key)
        binding = (method, path, action, param_map)
        starlette_routes.append(Route(path, _make_endpoint(binding), methods=[method]))

    app = Starlette(routes=starlette_routes)
    app.state.terminal = state

    if HTTP_CORS_ORIGINS:
        origins = (
            ["*"]
            if HTTP_CORS_ORIGINS == "*"
            else [o.strip() for o in HTTP_CORS_ORIGINS.split(",") if o.strip()]
        )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    if HTTP_API_KEY:
        app.add_middleware(ApiKeyMiddleware, api_key=HTTP_API_KEY)

    return app
