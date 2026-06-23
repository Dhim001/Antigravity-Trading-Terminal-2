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
from app.config import (
    ALLOW_CUSTOM_STRATEGIES,
    ALLOW_LIVE_BOTS,
    ARCHIVE_BACKEND,
    ARCHIVE_PARQUET_ENABLED,
    ARCHIVE_TICKS_ENABLED,
    BOT_MIN_CANDLES,
    HTTP_API_KEY,
    HTTP_CORS_ORIGINS,
    HTTP_HOST,
    HTTP_PORT,
    REDIS_URL,
    TERMINAL_MODE,
    TERMINAL_ROLE,
    WS_HOST,
    WS_PORT,
)
from app.api.http.auth import ApiKeyMiddleware
from app.database import get_db_stats
from app.services.bots.strategy_catalog import list_strategy_catalog
from app.services.bots.backtest_store import get_backtest_run, get_backtest_trades, list_backtest_runs
from app.services.bots.optimization_store import get_optimization_run, list_optimization_runs
from app.services.bots.backtest_job_store import get_active_backtest_job, get_backtest_job, list_backtest_jobs
from app.api.http.session import session_handler
from app.services.bots.calibration import (
    compute_calibration_apply_patch,
    get_calibration,
    get_filter_reject_dashboard,
)
from app.services.events import channels

ensure_routes_loaded()


async def metrics(request: Request) -> PlainTextResponse:
    from app.observability.metrics import render_prometheus

    return PlainTextResponse(render_prometheus(), media_type="text/plain; version=0.0.4")


async def health_live(request: Request) -> JSONResponse:
    """Fast liveness probe — no DB or LLM."""
    return JSONResponse({"ok": True, "service": "trading-terminal"})


async def health(request: Request) -> JSONResponse:
    import asyncio
    import time

    from app.services.agent.llm.router import get_llm_status

    state: AppState = request.app.state.terminal
    body = {
        "ok": True,
        "service": "trading-terminal",
        "terminal_mode": TERMINAL_MODE,
        "terminal_role": TERMINAL_ROLE,
        "ws_clients": len(state.manager.connected_clients),
        "websocket": f"ws://{WS_HOST}:{WS_PORT}",
        "http": f"http://{HTTP_HOST}:{HTTP_PORT}",
        "allow_live_bots": ALLOW_LIVE_BOTS,
        "allow_custom_strategies": ALLOW_CUSTOM_STRATEGIES,
        "archive_parquet_enabled": ARCHIVE_PARQUET_ENABLED,
        "archive_backend": ARCHIVE_BACKEND,
        "bot_min_candles": BOT_MIN_CANDLES,
        "archive_ticks_enabled": ARCHIVE_TICKS_ENABLED,
    }

    try:
        from app.config import (
            AGENT_ENABLED,
            AGENT_LLM_ENABLED,
            AGENT_VISION_ENABLED,
            SCANNER_ENABLED,
        )

        body["llm"] = await get_llm_status()
        body["agent_llm_enabled"] = AGENT_LLM_ENABLED
        body["agent_vision_enabled"] = AGENT_VISION_ENABLED
        body["agent_enabled"] = AGENT_ENABLED
        body["scanner_enabled"] = SCANNER_ENABLED
    except Exception:
        body["llm"] = {"available": False, "provider": "off"}
        body["agent_llm_enabled"] = False
        body["agent_vision_enabled"] = False
        body["agent_enabled"] = False
        body["scanner_enabled"] = False

    try:
        stats = await asyncio.to_thread(get_db_stats)
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

    try:
        from app.observability.metrics import observability_snapshot

        snap = observability_snapshot()
        body["observability"] = {
            k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in snap.items()
            if v is not None
        }
    except Exception:
        pass

    feed = getattr(state, "feed", None)
    if feed is not None and hasattr(feed, "feed_lag_sec"):
        try:
            lag = feed.feed_lag_sec()
            if lag is not None:
                body["feed_lag_sec"] = round(float(lag), 2)
        except Exception:
            pass

    if TERMINAL_MODE == "LIVE_IB" and feed is not None and hasattr(feed, "ib_status"):
        try:
            body["ib"] = feed.ib_status
        except Exception:
            pass

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
    timeframe = request.query_params.get("timeframe") or None
    state: AppState = request.app.state.terminal
    analyst = state.chart_analyst
    if analyst is None:
        return JSONResponse({"ok": False, "error": "Chart analyst unavailable"}, status_code=503)
    insights = analyst.list_insights(symbol, limit=limit, timeframe=timeframe)
    return JSONResponse({"ok": True, "symbol": symbol, "insights": insights, "count": len(insights)})


async def list_llm_models(request: Request) -> JSONResponse:
    from app.services.agent.llm.router import list_all_models

    try:
        data = await list_all_models()
        return JSONResponse({"ok": True, **data})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)


async def llm_ops_status_handler(request: Request) -> JSONResponse:
    from app.services.agent.llm.ops import ollama_ops_status

    try:
        return JSONResponse(await ollama_ops_status())
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)


async def pull_llm_model_handler(request: Request) -> JSONResponse:
    from app.services.agent.llm.ops import pull_ollama_model

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)
    model = (body.get("model") or "").strip()
    if not model:
        return JSONResponse({"ok": False, "error": "model is required"}, status_code=400)
    result = await pull_ollama_model(model)
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)


async def set_llm_model(request: Request) -> JSONResponse:
    from app.services.agent.llm.router import list_all_models, set_preferred_model

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)
    model = (body.get("model") or "").strip()
    if not model:
        set_preferred_model(None)
    else:
        available = await list_all_models()
        all_names = set(available.get("ollama") or []) | set(available.get("openrouter") or [])
        if all_names and model not in all_names:
            return JSONResponse(
                {"ok": False, "error": f"Model {model!r} not in available models"},
                status_code=400,
            )
        set_preferred_model(model)
    data = await list_all_models()
    return JSONResponse({"ok": True, **data})


async def list_backtest_runs_handler(request: Request) -> JSONResponse:
    symbol = request.query_params.get("symbol")
    try:
        limit = int(request.query_params.get("limit", "50"))
    except (TypeError, ValueError):
        limit = 50
    runs = list_backtest_runs(limit=limit, symbol=symbol or None)
    return JSONResponse({"ok": True, "runs": runs, "count": len(runs)})


async def get_backtest_run_handler(request: Request) -> JSONResponse:
    run_id = request.path_params.get("run_id")
    if not run_id:
        return JSONResponse({"ok": False, "error": "run_id is required"}, status_code=400)
    run = get_backtest_run(run_id)
    if not run:
        return JSONResponse({"ok": False, "error": "Backtest run not found"}, status_code=404)
    results = dict(run.get("results") or {})
    all_trades = results.get("trades") or []
    results["trades"] = all_trades[-100:]
    results["trades_total"] = len(all_trades)
    return JSONResponse({
        "ok": True,
        "run": {
            **run,
            "results": results,
        },
    })


async def get_backtest_trades_handler(request: Request) -> JSONResponse:
    run_id = request.path_params.get("run_id")
    if not run_id:
        return JSONResponse({"ok": False, "error": "run_id is required"}, status_code=400)
    trades = get_backtest_trades(run_id)
    if not trades and get_backtest_run(run_id) is None:
        return JSONResponse({"ok": False, "error": "Backtest run not found"}, status_code=404)
    return JSONResponse({
        "ok": True,
        "run_id": run_id,
        "trades": trades,
        "count": len(trades),
    })


async def get_active_backtest_job_handler(request: Request) -> JSONResponse:
    job = get_active_backtest_job()
    if not job:
        return JSONResponse({"ok": True, "job": None})
    return JSONResponse({"ok": True, "job": job})


async def get_backtest_job_handler(request: Request) -> JSONResponse:
    job_id = request.path_params.get("job_id")
    if not job_id:
        return JSONResponse({"ok": False, "error": "job_id is required"}, status_code=400)
    job = get_backtest_job(job_id)
    if not job:
        return JSONResponse({"ok": False, "error": "Backtest job not found"}, status_code=404)
    return JSONResponse({"ok": True, "job": job})


async def list_backtest_jobs_handler(request: Request) -> JSONResponse:
    status = request.query_params.get("status")
    try:
        limit = int(request.query_params.get("limit", "20"))
    except (TypeError, ValueError):
        limit = 20
    jobs = list_backtest_jobs(limit=limit, status=status or None)
    return JSONResponse({"ok": True, "jobs": jobs, "count": len(jobs)})


async def list_optimization_runs_handler(request: Request) -> JSONResponse:
    symbol = request.query_params.get("symbol")
    try:
        limit = int(request.query_params.get("limit", "20"))
    except (TypeError, ValueError):
        limit = 20
    runs = list_optimization_runs(limit=limit, symbol=symbol or None)
    return JSONResponse({"ok": True, "runs": runs, "count": len(runs)})


async def get_optimization_run_handler(request: Request) -> JSONResponse:
    run_id = request.path_params.get("run_id")
    if not run_id:
        return JSONResponse({"ok": False, "error": "run_id is required"}, status_code=400)
    run = get_optimization_run(run_id)
    if not run:
        return JSONResponse({"ok": False, "error": "Optimization run not found"}, status_code=404)
    return JSONResponse({"ok": True, "run": run})


async def get_bot_calibration_handler(request: Request) -> JSONResponse:
    bot_id = request.query_params.get("bot_id") or None
    symbol = request.query_params.get("symbol") or None
    try:
        min_samples = int(request.query_params.get("min_samples", "3"))
    except (TypeError, ValueError):
        min_samples = 3
    try:
        limit = int(request.query_params.get("limit", "2000"))
    except (TypeError, ValueError):
        limit = 2000
    data = get_calibration(
        bot_id=bot_id,
        symbol=symbol,
        min_samples=max(1, min_samples),
        limit=limit,
    )
    return JSONResponse({"ok": True, "calibration": data})


async def get_filter_rejects_handler(request: Request) -> JSONResponse:
    bot_id = request.query_params.get("bot_id") or None
    symbol = request.query_params.get("symbol") or None
    strategy = request.query_params.get("strategy") or None
    data = get_filter_reject_dashboard(bot_id=bot_id, symbol=symbol, strategy=strategy)
    return JSONResponse({"ok": True, "filter_rejects": data})


async def apply_calibration_suggestions_handler(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    bot_id = body.get("bot_id")
    if not bot_id:
        return JSONResponse({"ok": False, "error": "bot_id is required"}, status_code=400)

    symbol = body.get("symbol") or None
    kinds = body.get("kinds")
    if kinds is not None and not isinstance(kinds, list):
        return JSONResponse({"ok": False, "error": "kinds must be an array"}, status_code=400)
    apply_all = bool(body.get("apply_all"))
    try:
        min_samples = int(body.get("min_samples", 3))
    except (TypeError, ValueError):
        min_samples = 3

    try:
        result = compute_calibration_apply_patch(
            str(bot_id),
            symbol=symbol,
            kinds=kinds,
            apply_all=apply_all,
            min_samples=max(1, min_samples),
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    patch = result.get("patch") or {}
    if not patch:
        return JSONResponse({
            "ok": True,
            "applied": [],
            "patch": {},
            "message": result.get("message", "No suggestions to apply."),
        })

    state: AppState = request.app.state.terminal
    try:
        detail = await state.bot_manager.update_bot_config(str(bot_id), patch)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    return JSONResponse({
        "ok": True,
        "patch": patch,
        "applied": result.get("applied") or [],
        "message": result.get("message"),
        "bot": detail,
    })


async def agent_pipeline_scan_deploy_handler(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    symbols = body.get("symbols")
    if not symbols:
        from app.config import SYMBOLS

        symbols = list(SYMBOLS.keys())
    if isinstance(symbols, str):
        symbols = [s.strip() for s in symbols.split(",") if s.strip()]
    if not isinstance(symbols, list) or not symbols:
        return JSONResponse({"ok": False, "error": "symbols required"}, status_code=400)

    state: AppState = request.app.state.terminal
    from app.api.handlers.scanner import get_scanner
    from app.services.agent.pipeline import active_bot_symbols, deploy_from_scan

    try:
        max_deploy = max(0, min(int(body.get("max_deploy", 3)), 20))
    except (TypeError, ValueError):
        max_deploy = 3
    try:
        min_confidence = float(body.get("min_confidence", 0.6))
    except (TypeError, ValueError):
        min_confidence = 0.6
    try:
        min_score = int(body.get("min_score", 2))
    except (TypeError, ValueError):
        min_score = 2
    try:
        allocation = float(body.get("allocation", 1000))
    except (TypeError, ValueError):
        allocation = 1000.0

    scanner = get_scanner(getattr(state.oms, "feed", None))
    result = await deploy_from_scan(
        state.bot_manager,
        scanner,
        symbols=[str(s).upper() for s in symbols],
        strategy=str(body.get("strategy") or "CHART_AGENT"),
        timeframe=str(body.get("timeframe") or "1m"),
        allocation=allocation,
        max_deploy=max_deploy,
        signal_filter=str(body.get("signal_filter") or "ACTIONABLE"),
        min_confidence=min_confidence,
        min_score=min_score,
        base_config=body.get("config") if isinstance(body.get("config"), dict) else None,
        skip_existing=bool(body.get("skip_existing", True)),
        dry_run=bool(body.get("dry_run")),
        regime_routing=bool(body.get("regime_routing", True)),
    )
    return JSONResponse({"ok": True, "pipeline": result})


async def agent_pipeline_status_handler(request: Request) -> JSONResponse:
    state: AppState = request.app.state.terminal
    from app.services.agent.pipeline import active_bot_symbols

    strategy = request.query_params.get("strategy") or "CHART_AGENT"
    timeframe = request.query_params.get("timeframe") or None
    symbols = sorted(active_bot_symbols(state.bot_manager, strategy=strategy, timeframe=timeframe))
    bots = []
    for bot in state.bot_manager.active_bots.values():
        cfg = bot.get("config") or {}
        if cfg.get("pipeline_source"):
            bots.append({
                "bot_id": bot.get("id"),
                "symbol": bot.get("symbol"),
                "strategy": bot.get("strategy"),
                "timeframe": bot.get("timeframe"),
                "status": bot.get("status"),
                "pipeline_source": cfg.get("pipeline_source"),
            })
    return JSONResponse({
        "ok": True,
        "active_symbols": symbols,
        "pipeline_bots": bots,
    })


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
        Route("/health/live", health_live, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/api/v1/session", session_handler, methods=["GET"]),
        Route("/metrics", metrics, methods=["GET"]),
        Route("/api/v1/strategies", list_strategies, methods=["GET"]),
        Route("/api/v1/agent/insights/{symbol}", list_agent_insights, methods=["GET"]),
        Route("/api/v1/llm/models", list_llm_models, methods=["GET"]),
        Route("/api/v1/llm/ops", llm_ops_status_handler, methods=["GET"]),
        Route("/api/v1/llm/pull", pull_llm_model_handler, methods=["POST"]),
        Route("/api/v1/llm/model", set_llm_model, methods=["POST"]),
        Route("/api/v1/backtest/runs", list_backtest_runs_handler, methods=["GET"]),
        Route("/api/v1/backtest/runs/{run_id}", get_backtest_run_handler, methods=["GET"]),
        Route("/api/v1/backtest/runs/{run_id}/trades", get_backtest_trades_handler, methods=["GET"]),
        Route("/api/v1/backtest/jobs", list_backtest_jobs_handler, methods=["GET"]),
        Route("/api/v1/backtest/jobs/active", get_active_backtest_job_handler, methods=["GET"]),
        Route("/api/v1/backtest/jobs/{job_id}", get_backtest_job_handler, methods=["GET"]),
        Route("/api/v1/backtest/optimizations", list_optimization_runs_handler, methods=["GET"]),
        Route("/api/v1/backtest/optimizations/{run_id}", get_optimization_run_handler, methods=["GET"]),
        Route("/api/v1/bots/calibration", get_bot_calibration_handler, methods=["GET"]),
        Route("/api/v1/bots/calibration/apply", apply_calibration_suggestions_handler, methods=["POST"]),
        Route("/api/v1/bots/filter-rejects", get_filter_rejects_handler, methods=["GET"]),
        Route("/api/v1/agent/pipeline/scan-deploy", agent_pipeline_scan_deploy_handler, methods=["POST"]),
        Route("/api/v1/agent/pipeline/status", agent_pipeline_status_handler, methods=["GET"]),
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
