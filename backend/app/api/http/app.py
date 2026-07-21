"""Starlette HTTP API — auto-routed from HTTP_BINDINGS."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from app.api.http.bindings import HTTP_BINDINGS
from app.api.http.dispatch import http_status_and_body, invoke_action
from app.api.openapi import build_openapi_spec
from app.services.bots.execution_mode import execution_mode_label

from app.api.router import ensure_routes_loaded, list_routes
from app.api.state import AppState
from app.config import (
    ALLOW_CUSTOM_STRATEGIES,
    ALLOW_LIVE_BOTS,
    ARCHIVE_BACKEND,
    ARCHIVE_PARQUET_ENABLED,
    ARCHIVE_TICKS_ENABLED,
    BOT_MIN_CANDLES,
    OPERATOR_MODE,
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

logger = logging.getLogger(__name__)
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
from app.api.http.workspaces import get_workspaces_handler, save_workspace_handler, delete_workspace_handler
from app.services.events import channels

ensure_routes_loaded()

# Bound concurrent async ML train/validate tasks (APP_SCAN #40).
_ml_async_active = 0
_ml_async_guard = asyncio.Lock()


async def _reserve_ml_async_slot() -> bool:
    global _ml_async_active
    from app.config import ML_ASYNC_MAX_INFLIGHT

    limit = max(1, int(ML_ASYNC_MAX_INFLIGHT))
    async with _ml_async_guard:
        if _ml_async_active >= limit:
            return False
        _ml_async_active += 1
        return True


async def _release_ml_async_slot() -> None:
    global _ml_async_active
    async with _ml_async_guard:
        _ml_async_active = max(0, _ml_async_active - 1)


async def metrics(request: Request) -> PlainTextResponse:
    from app.observability.metrics import render_prometheus

    return PlainTextResponse(render_prometheus(), media_type="text/plain; version=0.0.4")


def _cheap_feed_snapshot(state: AppState) -> dict:
    """In-memory feed fields safe for high-frequency probes (no DB/LLM)."""
    body: dict = {
        "ok": True,
        "service": "trading-terminal",
        "terminal_mode": TERMINAL_MODE,
        "ws_clients": len(state.manager.connected_clients),
    }
    feed = getattr(state, "feed", None)
    if feed is not None and hasattr(feed, "feed_lag_sec"):
        try:
            lag = feed.feed_lag_sec()
            if lag is not None:
                body["feed_lag_sec"] = round(float(lag), 2)
        except Exception:
            pass
    if TERMINAL_MODE == "LIVE_MASSIVE" and feed is not None and hasattr(feed, "massive_status"):
        try:
            body["massive"] = feed.massive_status
        except Exception:
            body["massive"] = None
    if TERMINAL_MODE == "LIVE_IB" and feed is not None and hasattr(feed, "ib_status"):
        try:
            body["ib"] = feed.ib_status
        except Exception:
            body["ib"] = None
    try:
        from app.services.memory_snapshot import memory_subsystem_snapshot

        body["memory_subsystems"] = memory_subsystem_snapshot(state)
    except Exception:
        pass
    return body


async def health_live(request: Request) -> JSONResponse:
    """Fast liveness probe — no DB or LLM. Includes cheap in-memory feed fields."""
    state: AppState = request.app.state.terminal
    return JSONResponse(_cheap_feed_snapshot(state))


async def health_massive(request: Request) -> JSONResponse:
    """Lightweight Massive feed status for UI banners (no DB/LLM)."""
    state: AppState = request.app.state.terminal
    body = _cheap_feed_snapshot(state)
    if "massive" not in body:
        body["massive"] = None
    return JSONResponse(body)


# Full /health response cache — prevents poll storms from saturating the SQLite thread.
_HEALTH_CACHE_TTL_SEC = 10.0
_health_cache_body: dict | None = None
_health_cache_ts: float = 0.0
# Create the lock at import time so concurrent first hits cannot race two Lock()s.
# (Lazy init of asyncio.Lock without a sync gate is racy under concurrent awaits.)
_health_cache_lock = asyncio.Lock()


async def admin_shutdown_handler(request: Request) -> JSONResponse:
    """Local dev helper — request graceful shutdown (used by start-*.ps1 scripts)."""
    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1", "localhost"):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    state: AppState = request.app.state.terminal
    event = getattr(state, "shutdown_event", None)
    if event is None:
        return JSONResponse({"ok": False, "error": "shutdown not configured"}, status_code=503)

    import asyncio

    if not event.is_set():
        asyncio.get_running_loop().call_soon(event.set)
    return JSONResponse({"ok": True, "message": "shutdown requested"})


async def _build_health_body(state: AppState) -> dict:
    from app.services.agent.llm.router import get_llm_status

    body = {
        "ok": True,
        "service": "trading-terminal",
        "terminal_mode": TERMINAL_MODE,
        "terminal_role": TERMINAL_ROLE,
        "execution_mode": execution_mode_label(),
        "ws_clients": len(state.manager.connected_clients),
        "websocket": f"ws://{WS_HOST}:{WS_PORT}",
        "http": f"http://{HTTP_HOST}:{HTTP_PORT}",
        "allow_live_bots": ALLOW_LIVE_BOTS,
        "allow_custom_strategies": ALLOW_CUSTOM_STRATEGIES,
        "archive_parquet_enabled": ARCHIVE_PARQUET_ENABLED,
        "archive_backend": ARCHIVE_BACKEND,
        "bot_min_candles": BOT_MIN_CANDLES,
        "archive_ticks_enabled": ARCHIVE_TICKS_ENABLED,
        "operator_mode": OPERATOR_MODE,
    }

    try:
        from app.config import (
            AGENT_ENABLED,
            AGENT_LLM_ENABLED,
            AGENT_VISION_ENABLED,
            SCANNER_ENABLED,
        )

        body["llm"] = await asyncio.wait_for(get_llm_status(), timeout=2.0)
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
        from app.db.connection import check_db_health
        from app.db.async_bridge import run_db

        body["database"] = await run_db(check_db_health, light=True)
    except Exception as exc:
        body["database"] = {"ok": False, "error": str(exc)}
        body["ok"] = False

    try:
        from app.db.async_bridge import run_db
        from app.services.db_stats_cache import get_db_stats_cached

        # Skip archive COUNT(*) on the hot health path — use light metrics only.
        stats = await run_db(get_db_stats_cached, include_archive=False)
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
        from app.observability.ws_metrics import ws_metrics_snapshot

        snap = observability_snapshot()
        body["observability"] = {
            k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in snap.items()
            if v is not None
        }
        body["websocket_clients"] = ws_metrics_snapshot(
            connected=len(state.manager.connected_clients),
        )
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

    if TERMINAL_MODE == "LIVE_MASSIVE" and feed is not None and hasattr(feed, "massive_status"):
        try:
            body["massive"] = feed.massive_status
            ht_cache = getattr(feed, "_ht_cache", None)
            if isinstance(ht_cache, dict):
                body["massive"]["ht_cache_entries"] = len(ht_cache)
        except Exception:
            pass
        try:
            from app.services.massive_ht_limits import massive_ht_limits_summary

            body["massive_ht_limits"] = massive_ht_limits_summary()
        except Exception:
            pass

    try:
        from app.services.memory_snapshot import memory_subsystem_snapshot

        body["memory_subsystems"] = memory_subsystem_snapshot(state)
    except Exception as exc:
        body["memory_subsystems"] = {"error": str(exc)}

    return body


async def health(request: Request) -> JSONResponse:
    """Full diagnostics probe — cached to avoid SQLite/LLM storms from UI pollers."""
    global _health_cache_body, _health_cache_ts

    force = request.query_params.get("fresh") in ("1", "true", "yes")
    now = time.monotonic()
    if (
        not force
        and _health_cache_body is not None
        and (now - _health_cache_ts) < _HEALTH_CACHE_TTL_SEC
    ):
        cached = dict(_health_cache_body)
        # Refresh live client count even on cache hit.
        state: AppState = request.app.state.terminal
        cached["ws_clients"] = len(state.manager.connected_clients)
        cached["cached"] = True
        return JSONResponse(cached)

    async with _health_cache_lock:
        now = time.monotonic()
        if (
            not force
            and _health_cache_body is not None
            and (now - _health_cache_ts) < _HEALTH_CACHE_TTL_SEC
        ):
            cached = dict(_health_cache_body)
            state = request.app.state.terminal
            cached["ws_clients"] = len(state.manager.connected_clients)
            cached["cached"] = True
            return JSONResponse(cached)

        state = request.app.state.terminal
        body = await _build_health_body(state)
        body["cached"] = False
        _health_cache_body = body
        _health_cache_ts = time.monotonic()
        return JSONResponse(body)


async def list_strategies(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "strategies": list_strategy_catalog()})


async def ml_model_status(request: Request) -> JSONResponse:
    """GET /api/v1/ml/model-status?symbol=X&strategy=Y&timeframe=15m — check if model exists."""
    symbol = (request.query_params.get("symbol") or "").upper()
    strategy = (request.query_params.get("strategy") or "").upper()
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe

    timeframe = normalize_model_timeframe(request.query_params.get("timeframe"))
    if not symbol or not strategy:
        return JSONResponse({"ok": False, "error": "symbol and strategy required"}, status_code=400)

    model_loaders = {
        "ML_SIGNAL_BOOST": lambda s, tf: _ml_model_status_xgb(s, timeframe=tf),
        "LSTM_DIRECTION": lambda s, tf: _ml_model_status_onnx(
            s, "lstm_signal_models", onnx_name="lstm_direction.onnx", timeframe=tf,
        ),
        "RL_PPO_AGENT": lambda s, tf: _ml_model_status_onnx(
            s, "rl_ppo_models", onnx_name="ppo_policy.onnx", timeframe=tf,
        ),
        "TCN_MULTI_HORIZON": lambda s, tf: _ml_model_status_onnx(
            s, "tcn_signal_models", onnx_name="tcn_multi_horizon.onnx", timeframe=tf,
        ),
        "VAE_REGIME_DETECTOR": lambda s, tf: _ml_model_status_onnx(
            s, "vae_regime_models", onnx_name="vae_regime.onnx", timeframe=tf,
        ),
        "TRANSFORMER_SIGNAL": lambda s, tf: _ml_model_status_onnx(
            s, "transformer_signal_models", onnx_name="transformer_signal.onnx", timeframe=tf,
        ),
        "GNN_CROSS_ASSET": lambda s, tf: _ml_model_status_onnx(
            s, "gnn_signal_models", onnx_name="gnn_cross_asset.onnx", timeframe=tf,
        ),
    }
    loader = model_loaders.get(strategy)
    if not loader:
        return JSONResponse({"ok": True, "trained": False, "error": "unknown strategy"})

    result = loader(symbol, timeframe)
    result["timeframe"] = timeframe
    return JSONResponse({"ok": True, **result})


def _ml_status_enrich(model_dir: str, meta: dict, artifact: str | None) -> dict:
    from app.services.bots.ml_model_artifacts import (
        apply_validation_sidecar,
        dataset_summary_from_metadata,
        list_model_versions,
        validation_summary_from_metadata,
    )

    meta = apply_validation_sidecar(meta if isinstance(meta, dict) else {}, model_dir)
    versions = list_model_versions(model_dir)
    try:
        dataset = dataset_summary_from_metadata(meta)
    except Exception:
        dataset = None
    try:
        validation = validation_summary_from_metadata(meta)
    except Exception:
        validation = {"validated_at": None, "walk_forward": None, "pbo": None}
    return {
        "trained": True,
        "trained_at": meta.get("trained_at"),
        "model_version": meta.get("trained_at"),
        "version_id": meta.get("version_id"),
        "metrics": meta.get("metrics", {}),
        "loss_history": meta.get("loss_history") or meta.get("train_history"),
        "train_history": meta.get("train_history"),
        "artifact": artifact,
        "model_type": meta.get("model_type"),
        "versions": versions,
        "dataset": dataset,
        # Deploy-readiness (additive — older UIs ignore these keys).
        "validated_at": validation.get("validated_at"),
        "walk_forward": validation.get("walk_forward"),
        "pbo": validation.get("pbo"),
    }


def _ml_model_status_xgb(symbol: str, *, timeframe: str | None = None) -> dict:
    import os, json
    from app.services.bots.ml_model_artifacts import model_root_for

    model_dir = model_root_for("ML_SIGNAL_BOOST", symbol, timeframe) or ""
    meta_path = os.path.join(model_dir, "metadata.json") if model_dir else ""
    if not model_dir or not os.path.isfile(meta_path):
        return {"trained": False, "versions": [], "dataset": None}
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        artifact = "model.joblib"
        art_path = os.path.join(model_dir, artifact)
        out = _ml_status_enrich(model_dir, meta, artifact if os.path.isfile(art_path) else None)
        out["model_type"] = meta.get("model_type") or "xgboost"
        return out
    except Exception:
        return {"trained": False, "versions": [], "dataset": None}


def _ml_model_status_onnx(
    symbol: str,
    subdir: str,
    onnx_name: str = "model.onnx",
    *,
    timeframe: str | None = None,
) -> dict:
    import os, json
    from app.config import BASE_DIR
    from app.services.bots.ml_model_artifacts import model_storage_key

    safe = model_storage_key(symbol, timeframe)
    model_dir = os.path.join(BASE_DIR, "data", subdir, safe)
    meta_path = os.path.join(model_dir, "metadata.json")
    if not os.path.isfile(meta_path):
        return {"trained": False, "versions": [], "dataset": None}
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        art_path = os.path.join(model_dir, onnx_name)
        return _ml_status_enrich(
            model_dir, meta, onnx_name if os.path.isfile(art_path) else None
        )
    except Exception:
        return {"trained": False, "versions": [], "dataset": None}


def _normalize_ml_symbol(symbol: str) -> str:
    """Map bare crypto bases to terminal pairs; leave equities unchanged."""
    from app.config import CRYPTO_SYMBOLS, _normalize_crypto_watch_symbol
    from app.services.massive_symbols import is_crypto_terminal_symbol

    raw = (symbol or "").strip().upper()
    if not raw:
        return raw
    if (
        raw in CRYPTO_SYMBOLS
        or is_crypto_terminal_symbol(raw)
        or f"{raw}USDT" in CRYPTO_SYMBOLS
        or raw in {"BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE"}
    ):
        return _normalize_crypto_watch_symbol(raw) or raw
    return raw


def _parse_ml_request_body(raw) -> tuple[dict | None, str | None]:
    """Accept a JSON object, or a double-encoded JSON string from older clients."""
    import json as _json

    body = raw
    if isinstance(body, str):
        try:
            body = _json.loads(body)
        except Exception:
            return None, "invalid JSON body"
    if not isinstance(body, dict):
        return None, "JSON body must be an object"
    return body, None


async def ml_train_handler(request: Request) -> JSONResponse:
    """POST /api/v1/ml/train — trigger model training for a strategy + symbol."""
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

    body, err = _parse_ml_request_body(raw)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)

    symbol = _normalize_ml_symbol(body.get("symbol") or "")
    strategy = (body.get("strategy") or "").upper()
    if not symbol or not strategy:
        return JSONResponse({"ok": False, "error": "symbol and strategy required"}, status_code=400)

    config = body.get("config") if isinstance(body.get("config"), dict) else {}
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe
    from app.services.bots.ml_training_window import (
        bar_limit_for_training_window,
        parse_training_window_months,
        summarize_training_window,
    )

    win_months = parse_training_window_months(config)
    tf = normalize_model_timeframe(
        config.get("timeframe") or body.get("timeframe")
    )
    bar_limit = bar_limit_for_training_window(win_months, timeframe=tf, purpose="train")

    # Fetch candles from live feed / archive sized to Lab training window + TF
    state: AppState = request.app.state.terminal
    trainers = {
        "ML_SIGNAL_BOOST": _train_xgb,
        "LSTM_DIRECTION": _train_lstm,
        "RL_PPO_AGENT": _train_ppo,
        "TCN_MULTI_HORIZON": _train_tcn,
        "VAE_REGIME_DETECTOR": _train_vae,
        "TRANSFORMER_SIGNAL": _train_transformer,
        "GNN_CROSS_ASSET": _train_gnn,
    }
    if strategy not in trainers:
        return JSONResponse({"ok": False, "error": f"training not supported for {strategy}"}, status_code=400)

    from app.services.bots.ml_train_executor import submit_train_job

    async_mode = bool(body.get("async"))
    event_bus = getattr(state, "event_bus", None)
    config = {
        **config,
        "timeframe": tf,
        "training_window_months": win_months,
    }

    if async_mode:
        from app.services.bots.ml_job_progress import make_progress_path, write_ml_progress
        from app.services.bots.ml_job_store import (
            create_ml_job,
            finish_ml_job,
            mark_ml_job_running,
            update_ml_job_progress,
        )

        if not await _reserve_ml_async_slot():
            return JSONResponse(
                {
                    "ok": False,
                    "error": "async ML queue full — wait for an in-flight job or raise ML_ASYNC_MAX_INFLIGHT",
                    "retry": True,
                },
                status_code=429,
            )

        progress_path = make_progress_path(f"train_{symbol}")
        job_id = create_ml_job(
            kind="train",
            strategy=strategy,
            symbol=symbol,
            progress_path=progress_path,
        )
        write_ml_progress(progress_path, pct=1, phase="queued", detail="starting")
        update_ml_job_progress(job_id, {"pct": 1, "phase": "queued", "detail": "starting"})

        async def _bg_train() -> None:
            cfg = dict(config)
            try:
                mark_ml_job_running(job_id)
                write_ml_progress(
                    progress_path, pct=2, phase="fetch",
                    detail=f"candles ≤{bar_limit} bars",
                )
                update_ml_job_progress(
                    job_id,
                    {"pct": 2, "phase": "fetch", "detail": f"candles ≤{bar_limit} bars"},
                )
                candles = await _fetch_training_candles(
                    state, symbol, tf=tf, months=win_months, limit=bar_limit,
                )
                if len(candles) < 200:
                    finish_ml_job(
                        job_id,
                        "error",
                        result={"ok": False, "error": f"insufficient candles ({len(candles)})"},
                        error=f"insufficient candles ({len(candles)})",
                    )
                    return
                write_ml_progress(progress_path, pct=4, phase="enrich", detail="indicators")
                candles = _enrich_training_candles(symbol, candles, strategy, cfg)
                window_meta = summarize_training_window(
                    candles, win_months, bar_limit=bar_limit, timeframe=tf,
                )
                cfg["_training_window"] = window_meta
                cfg["_progress_path"] = progress_path
                write_ml_progress(
                    progress_path, pct=5, phase="train",
                    detail=f"{strategy} · {len(candles)} bars",
                )
                await submit_train_job(
                    strategy, symbol, candles, cfg,
                    job_id=job_id, event_bus=event_bus,
                )
            except Exception as exc:
                logger.exception("Async ML train job %s failed", job_id)
                finish_ml_job(
                    job_id,
                    "error",
                    result={"ok": False, "error": str(exc)},
                    error=str(exc),
                )
            finally:
                await _release_ml_async_slot()

        asyncio.create_task(_bg_train())
        return JSONResponse({
            "ok": True,
            "job_id": job_id,
            "async": True,
        })

    try:
        candles = await _fetch_training_candles(
            state, symbol, tf=tf, months=win_months, limit=bar_limit,
        )
    except Exception as exc:
        logger.exception("Failed to fetch training candles for %s", symbol)
        return JSONResponse({"ok": False, "error": f"failed to fetch candles: {exc}"}, status_code=500)

    if len(candles) < 200:
        return JSONResponse({"ok": False, "error": f"insufficient candles ({len(candles)})"}, status_code=400)

    try:
        candles = _enrich_training_candles(symbol, candles, strategy, config)
    except Exception as exc:
        logger.exception("Failed to enrich training candles for %s", symbol)
        return JSONResponse({"ok": False, "error": f"indicator enrichment failed: {exc}"}, status_code=500)

    window_meta = summarize_training_window(
        candles, win_months, bar_limit=bar_limit, timeframe=tf,
    )
    config = {
        **config,
        "_training_window": window_meta,
    }

    try:
        result = await submit_train_job(
            strategy, symbol, candles, config, event_bus=event_bus,
        )
    except Exception as exc:
        logger.exception("ML training failed for %s/%s", strategy, symbol)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    try:
        import json as _json
        if isinstance(result, dict):
            result = {**result, "training_window": window_meta}
        payload = _json.loads(_json.dumps(result, default=str))
    except Exception:
        payload = {"ok": bool(result.get("ok")), "error": "training result not serializable"}
    return JSONResponse(payload)


async def _fetch_training_candles(
    state: AppState,
    symbol: str,
    tf: str = "1m",
    limit: int | None = None,
    *,
    months: int | None = None,
) -> list[dict]:
    """Pull historical candles for training from feed, archive, or Massive/Binance REST.

    When ``months`` is set (ML Lab Training window), sizes the request and
    time-trims to that calendar window. ``limit`` overrides the bar target
    when provided explicitly.
    """
    import asyncio
    import time

    from app.config import TERMINAL_MODE
    from app.services.bots.candle_source import get_bot_candles
    from app.services.bots.ml_training_window import (
        bar_limit_for_training_window,
        parse_training_window_months,
        training_window_seconds,
        trim_candles_to_training_window,
    )
    from app.services.market.timeframes import normalize_timeframe

    symbol = _normalize_ml_symbol(symbol)
    tf = normalize_timeframe(tf or "1m")
    feed = getattr(state, "feed", None) or getattr(getattr(state, "oms", None), "feed", None)

    win_months = parse_training_window_months(
        {"training_window_months": months if months is not None else 3}
    )
    if limit is not None:
        min_bars = max(200, int(limit))
    else:
        min_bars = bar_limit_for_training_window(win_months, timeframe=tf, purpose="train")

    candles: list[dict] = []
    if feed is not None:
        candles = await asyncio.to_thread(
            get_bot_candles,
            symbol,
            feed,
            timeframe=tf,
            min_bars=min_bars,
        )
        candles = [dict(c) for c in (candles or [])]

    def _deep_rest() -> list[dict]:
        to_ts = int(time.time())
        try:
            from app.services.market.timeframes import timeframe_to_secs

            bar_secs = max(60, int(timeframe_to_secs(tf)))
        except Exception:
            bar_secs = 60
        span = max(training_window_seconds(win_months), int(min_bars * bar_secs * 1.25))
        from_ts = to_ts - span
        info = None
        if feed is not None:
            info = getattr(feed, "_symbols", {}).get(symbol)

        if TERMINAL_MODE == "LIVE_MASSIVE":
            from app.services.archive.broker_fetch import fetch_massive_tf_candles

            return fetch_massive_tf_candles(
                symbol, from_ts, to_ts, tf, symbol_info=info,
            ) or []

        from app.services.massive_symbols import is_crypto_terminal_symbol as _is_crypto
        from app.services.archive.broker_fetch import fetch_binance_1m_bars

        if _is_crypto(symbol) and tf == "1m":
            rows = fetch_binance_1m_bars(symbol, from_ts, to_ts) or []
            out = []
            for r in rows:
                out.append({
                    "time": int(r.get("time") or r.get("bar_time") or 0),
                    "open": float(r.get("open") or 0),
                    "high": float(r.get("high") or 0),
                    "low": float(r.get("low") or 0),
                    "close": float(r.get("close") or 0),
                    "volume": float(r.get("volume") or 0),
                })
            return out
        return []

    # Need a deep seed when archive/live buffer is shorter than the Lab window.
    if len(candles) < min_bars:
        deep = await asyncio.to_thread(_deep_rest)
        if deep:
            from app.services.archive.resolve import merge_candle_series

            candles = [dict(c) for c in merge_candle_series(deep, candles)]

    candles = trim_candles_to_training_window(candles, win_months)
    if len(candles) > min_bars:
        candles = candles[-min_bars:]
    return candles


def _enrich_training_candles(
    symbol: str, candles: list[dict], strategy: str, config: dict | None
) -> list[dict]:
    """Attach ATR / TA columns required by ML label + feature pipelines."""
    from app.services.bots.screener import MarketScreenerService

    screener = MarketScreenerService()
    df = screener.process_candles(
        symbol, candles, config or {}, strategy, full_history=True,
    )
    if df is None or getattr(df, "empty", True):
        out = [dict(c) for c in (candles or [])]
    else:
        out = [dict(r) for r in df.to_dict("records")]
    for row in out:
        row.setdefault("_symbol", symbol)
    return out

def _train_xgb(symbol, candles, config):
    from app.services.bots.strategies_ml import train_ml_signal_model
    return train_ml_signal_model(symbol, candles, config=config)


def _train_lstm(symbol, candles, config):
    from app.services.bots.ml_lstm_trainer import train_lstm_signal_model
    return train_lstm_signal_model(symbol, candles, config=config)


def _train_ppo(symbol, candles, config):
    from app.services.bots.rl_ppo_trainer import train_ppo_agent
    return train_ppo_agent(symbol, candles, config=config, total_timesteps=config.get("total_timesteps", 30000))


def _train_tcn(symbol, candles, config):
    from app.services.bots.ml_tcn_trainer import train_tcn_model
    return train_tcn_model(symbol, candles, config=config)


def _train_vae(symbol, candles, config):
    from app.services.bots.ml_vae_regime import train_vae_regime_model
    return train_vae_regime_model(symbol, candles, config=config)


def _train_transformer(symbol, candles, config):
    from app.services.bots.ml_transformer_trainer import train_transformer_model
    return train_transformer_model(symbol, candles, config=config)


def _train_gnn(symbol, candles, config):
    from app.services.bots.ml_gnn_trainer import train_gnn_model
    return train_gnn_model(symbol, candles, config=config)


def _run_ml_validate_job(
    strategy: str,
    symbol: str,
    candles: list[dict],
    config: dict,
    *,
    n_folds: int,
    mode: str,
    run_pbo: bool,
    pbo_segments: int,
) -> dict:
    """CPU-bound WF (+ optional PBO). Delegates to process-safe runner."""
    from app.services.bots.ml_train_executor import run_validate_job

    return run_validate_job(
        strategy,
        symbol,
        candles,
        config,
        n_folds,
        mode,
        run_pbo,
        pbo_segments,
    )


def _json_safe(value):
    """Coerce nested values into JSON-compliant types (no NaN/Inf/numpy)."""
    import math

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    # numpy scalars / paths / datetimes
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return _json_safe(value.item())
    except Exception:
        pass
    if hasattr(value, "item") and callable(value.item):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    return str(value)


def _ml_validate_json_response(payload: dict, *, status_code: int = 200) -> JSONResponse:
    """Always emit application/json — never let Starlette fall back to plain text 500."""
    try:
        safe = _json_safe(payload if isinstance(payload, dict) else {"ok": False, "error": "invalid result"})
        import json as _json

        # Round-trip guarantees the body is JSON-serializable.
        safe = _json.loads(_json.dumps(safe, allow_nan=False, default=str))
    except Exception:
        safe = {"ok": False, "error": "validation result not serializable"}
        status_code = 200
    return JSONResponse(safe, status_code=status_code)


async def ml_validate_handler(request: Request) -> JSONResponse:
    """POST /api/v1/ml/validate — run walk-forward validation + optional PBO."""
    try:
        try:
            raw = await request.json()
        except Exception:
            return _ml_validate_json_response({"ok": False, "error": "invalid JSON"}, status_code=400)

        body, err = _parse_ml_request_body(raw)
        if err:
            return _ml_validate_json_response({"ok": False, "error": err}, status_code=400)

        symbol = _normalize_ml_symbol(body.get("symbol") or "")
        strategy = (body.get("strategy") or "").upper()
        if not symbol or not strategy:
            return _ml_validate_json_response(
                {"ok": False, "error": "symbol and strategy required"},
                status_code=400,
            )

        try:
            n_folds = max(1, min(10, int(body.get("n_folds", 5))))
        except (TypeError, ValueError):
            n_folds = 5
        mode = str(body.get("mode", "rolling") or "rolling").lower()
        run_pbo = bool(body.get("pbo", False))
        config = body.get("config") if isinstance(body.get("config"), dict) else {}
        try:
            pbo_segments = max(2, min(12, int(body.get("pbo_segments", 6))))
        except (TypeError, ValueError):
            pbo_segments = 6

        from app.services.bots.ml_model_artifacts import normalize_model_timeframe
        from app.services.bots.ml_training_window import (
            bar_limit_for_training_window,
            parse_training_window_months,
            summarize_training_window,
        )

        win_months = parse_training_window_months(config)
        tf = normalize_model_timeframe(
            config.get("timeframe") or body.get("timeframe")
        )
        bar_limit = bar_limit_for_training_window(win_months, timeframe=tf, purpose="validate")
        # Honor Lab validate_max_bars. Do NOT inflate past the client value — that
        # made async Validate block for minutes on deep REST before returning job_id.
        try:
            user_vmax = int(config.get("validate_max_bars") or 0)
        except (TypeError, ValueError):
            user_vmax = 0
        if user_vmax > 0:
            vmax = min(user_vmax, bar_limit, 8_000)
        else:
            vmax = min(2_500, bar_limit)
        config = {
            **config,
            "timeframe": tf,
            "training_window_months": win_months,
            "validate_max_bars": vmax,
        }

        state: AppState = request.app.state.terminal
        from app.services.bots.ml_train_executor import submit_validate_job

        async_mode = bool(body.get("async"))
        event_bus = getattr(state, "event_bus", None)

        if async_mode:
            from app.services.bots.ml_job_progress import make_progress_path, write_ml_progress
            from app.services.bots.ml_job_store import (
                create_ml_job,
                finish_ml_job,
                mark_ml_job_running,
                update_ml_job_progress,
            )

            if not await _reserve_ml_async_slot():
                return _ml_validate_json_response(
                    {
                        "ok": False,
                        "error": "async ML queue full — wait for an in-flight job or raise ML_ASYNC_MAX_INFLIGHT",
                        "retry": True,
                    },
                    status_code=429,
                )

            progress_path = make_progress_path(f"validate_{symbol}")
            job_id = create_ml_job(
                kind="validate",
                strategy=strategy,
                symbol=symbol,
                progress_path=progress_path,
            )
            write_ml_progress(progress_path, pct=1, phase="queued", detail="starting")
            update_ml_job_progress(job_id, {"pct": 1, "phase": "queued", "detail": "starting"})

            async def _bg_validate() -> None:
                cfg = dict(config)
                try:
                    mark_ml_job_running(job_id)
                    write_ml_progress(
                        progress_path, pct=2, phase="fetch",
                        detail=f"candles ≤{vmax} bars",
                    )
                    update_ml_job_progress(
                        job_id, {"pct": 2, "phase": "fetch", "detail": f"candles ≤{vmax} bars"},
                    )
                    # Fetch only what WF will use — not the full Lab train window.
                    candles = await _fetch_training_candles(
                        state, symbol, tf=tf, months=win_months, limit=vmax,
                    )
                    write_ml_progress(progress_path, pct=4, phase="enrich", detail="indicators")
                    update_ml_job_progress(
                        job_id, {"pct": 4, "phase": "enrich", "detail": "indicators"},
                    )
                    candles = _enrich_training_candles(symbol, candles, strategy, cfg)
                    if len(candles) < 500:
                        finish_ml_job(
                            job_id,
                            "error",
                            result={
                                "ok": False,
                                "error": f"Need >= 500 candles for validation, got {len(candles)}",
                            },
                            error=f"Need >= 500 candles for validation, got {len(candles)}",
                        )
                        return
                    window_meta = summarize_training_window(
                        candles, win_months, bar_limit=vmax, timeframe=tf,
                    )
                    cfg["_training_window"] = window_meta
                    cfg["_progress_path"] = progress_path
                    for row in candles:
                        if isinstance(row, dict) and not row.get("_symbol"):
                            row["_symbol"] = symbol
                    write_ml_progress(
                        progress_path, pct=5, phase="validate",
                        detail=f"walk-forward · {len(candles)} bars",
                    )
                    await submit_validate_job(
                        strategy,
                        symbol,
                        candles,
                        cfg,
                        n_folds=n_folds,
                        mode=mode,
                        run_pbo=run_pbo,
                        pbo_segments=pbo_segments,
                        job_id=job_id,
                        event_bus=event_bus,
                    )
                except Exception as exc:
                    logger.exception("Async ML validate job %s failed", job_id)
                    finish_ml_job(
                        job_id,
                        "error",
                        result={"ok": False, "error": str(exc)},
                        error=str(exc),
                    )
                finally:
                    await _release_ml_async_slot()

            asyncio.create_task(_bg_validate())
            return _ml_validate_json_response({"ok": True, "job_id": job_id, "async": True})

        try:
            candles = await _fetch_training_candles(
                state, symbol, tf=tf, months=win_months, limit=vmax,
            )
            candles = _enrich_training_candles(symbol, candles, strategy, config)
        except Exception as exc:
            logger.exception("Failed to fetch/enrich candles for validate %s", symbol)
            return _ml_validate_json_response(
                {"ok": False, "error": f"failed to fetch candles: {exc}"},
            )

        if len(candles) < 500:
            return _ml_validate_json_response(
                {"ok": False, "error": f"Need >= 500 candles for validation, got {len(candles)}"},
                status_code=400,
            )

        window_meta = summarize_training_window(
            candles, win_months, bar_limit=vmax, timeframe=tf,
        )
        config["_training_window"] = window_meta
        for row in candles:
            if isinstance(row, dict) and not row.get("_symbol"):
                row["_symbol"] = symbol

        try:
            result = await submit_validate_job(
                strategy,
                symbol,
                candles,
                config,
                n_folds=n_folds,
                mode=mode,
                run_pbo=run_pbo,
                pbo_segments=pbo_segments,
                event_bus=event_bus,
            )
        except Exception as exc:
            logger.exception("ML validate failed for %s/%s", strategy, symbol)
            return _ml_validate_json_response(
                {
                    "ok": False,
                    "error": str(exc) or "Validation failed",
                    "strategy": strategy,
                    "symbol": symbol,
                },
            )

        if not isinstance(result, dict):
            return _ml_validate_json_response(
                {"ok": False, "error": "validation returned invalid result", "strategy": strategy, "symbol": symbol},
            )

        if result.get("ok") is False and not result.get("error"):
            folds = result.get("folds") if isinstance(result.get("folds"), list) else []
            fold_errs = [f.get("error") for f in folds if isinstance(f, dict) and f.get("error")]
            result["error"] = fold_errs[0] if fold_errs else "Validation failed"

        # Soft-fail: always 200 with ok flag so the UI can render fold details.
        return _ml_validate_json_response(result)
    except Exception as exc:
        logger.exception("ml_validate_handler crashed")
        return _ml_validate_json_response(
            {"ok": False, "error": f"Validation failed: {exc}"},
        )


async def ml_retrain_status_handler(request: Request) -> JSONResponse:
    """GET /api/v1/ml/retrain-status — list models needing retrain."""
    from app.services.bots.ml_retrain_scheduler import get_retrain_scheduler

    state: AppState = request.app.state.terminal
    bots = []
    if hasattr(state, "bot_manager") and state.bot_manager:
        for bot_id, bot in state.bot_manager.active_bots.items():
            bots.append({
                "strategy": bot.get("strategy"),
                "symbol": bot.get("symbol"),
            })

    scheduler = get_retrain_scheduler()
    actions = scheduler.check(bots)
    pending = scheduler.get_pending()
    history = scheduler.get_retrain_history(20)
    return JSONResponse({
        "ok": True,
        "retrain_actions": actions,
        # Additive — older UIs ignore these keys.
        "pending": pending,
        "history": history,
    })


async def ml_list_jobs_handler(request: Request) -> JSONResponse:
    """GET /api/v1/ml/jobs — recent jobs + queue depth."""
    from app.services.bots.ml_job_store import list_ml_jobs, ml_job_counts, public_ml_job

    try:
        limit = int(request.query_params.get("limit", "20"))
    except (TypeError, ValueError):
        limit = 20
    active_only = str(request.query_params.get("active") or "").lower() in ("1", "true", "yes")
    jobs = [public_ml_job(j, include_result=False) for j in list_ml_jobs(limit=limit, active_only=active_only)]
    counts = ml_job_counts()
    return JSONResponse({
        "ok": True,
        "jobs": jobs,
        "count": len(jobs),
        "active": counts["active"],
        "queued": counts["queued"],
    })


async def ml_get_job_handler(request: Request) -> JSONResponse:
    """GET /api/v1/ml/jobs/{job_id} — status / progress / result."""
    from app.services.bots.ml_job_store import get_ml_job, public_ml_job

    job_id = request.path_params.get("job_id")
    if not job_id:
        return JSONResponse({"ok": False, "error": "job_id required"}, status_code=400)
    job = get_ml_job(job_id)
    if not job:
        return JSONResponse({"ok": False, "error": "job not found"}, status_code=404)
    return JSONResponse({"ok": True, "job": public_ml_job(job, include_result=True)})


async def ml_cancel_job_handler(request: Request) -> JSONResponse:
    """POST /api/v1/ml/jobs/{job_id}/cancel — cooperative cancel."""
    from app.services.bots.ml_job_store import get_ml_job, public_ml_job, request_ml_job_cancel

    job_id = request.path_params.get("job_id")
    if not job_id:
        return JSONResponse({"ok": False, "error": "job_id required"}, status_code=400)
    if not get_ml_job(job_id):
        return JSONResponse({"ok": False, "error": "job not found"}, status_code=404)
    outcome = request_ml_job_cancel(job_id)
    job = get_ml_job(job_id)
    return JSONResponse({
        "ok": bool(outcome.get("ok")),
        **outcome,
        "job": public_ml_job(job, include_result=True),
    })


async def ml_list_runs_handler(request: Request) -> JSONResponse:
    """GET /api/v1/ml/runs — persistent train/validate history."""
    from app.services.bots.ml_train_runs import list_ml_train_runs

    symbol = (request.query_params.get("symbol") or "").strip().upper() or None
    strategy = (request.query_params.get("strategy") or "").strip().upper() or None
    timeframe = (request.query_params.get("timeframe") or "").strip() or None
    try:
        limit = int(request.query_params.get("limit", "20"))
    except (TypeError, ValueError):
        limit = 20
    runs = list_ml_train_runs(
        symbol=symbol, strategy=strategy, timeframe=timeframe, limit=limit,
    )
    return JSONResponse({"ok": True, "runs": runs, "count": len(runs)})


async def ml_activate_version_handler(request: Request) -> JSONResponse:
    """POST /api/v1/ml/activate-version — promote a snapshot to current root."""
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

    body, err = _parse_ml_request_body(raw)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)

    symbol = _normalize_ml_symbol(body.get("symbol") or "")
    strategy = (body.get("strategy") or "").upper()
    model_version = str(
        body.get("model_version") or body.get("version_id") or body.get("trained_at") or ""
    ).strip()
    from app.services.bots.ml_model_artifacts import (
        activate_model_version,
        invalidate_strategy_model_caches,
        model_root_for,
        normalize_model_timeframe,
    )

    timeframe = normalize_model_timeframe(
        body.get("timeframe")
        or (body.get("config") if isinstance(body.get("config"), dict) else {}).get("timeframe")
    )
    if not symbol or not strategy:
        return JSONResponse({"ok": False, "error": "symbol and strategy required"}, status_code=400)
    if not model_version:
        return JSONResponse({"ok": False, "error": "model_version required"}, status_code=400)

    if model_root_for(strategy, symbol, timeframe) is None:
        return JSONResponse(
            {"ok": False, "error": f"unknown strategy {strategy}"},
            status_code=400,
        )

    try:
        result = await asyncio.to_thread(
            activate_model_version, strategy, symbol, model_version, timeframe,
        )
    except Exception as exc:
        logger.exception("activate-version failed for %s/%s", strategy, symbol)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    if not result.get("ok"):
        return JSONResponse(result, status_code=404)

    invalidate_strategy_model_caches(strategy, symbol)

    # Enrich like model-status so the UI can refresh in one round-trip.
    root = model_root_for(strategy, symbol, timeframe)
    artifact = None
    if strategy == "ML_SIGNAL_BOOST":
        art = "model.joblib"
    else:
        from app.services.bots.ml_model_artifacts import STRATEGY_ARTIFACTS
        arts = STRATEGY_ARTIFACTS.get(strategy) or []
        art = next((a for a in arts if a.endswith(".onnx")), arts[0] if arts else None)
    if root and art and os.path.isfile(os.path.join(root, art)):
        artifact = art

    meta = {}
    meta_path = os.path.join(root, "metadata.json") if root else ""
    if meta_path and os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}

    enriched = _ml_status_enrich(root or "", meta if isinstance(meta, dict) else {}, artifact)
    return JSONResponse({
        "ok": True,
        **enriched,
        "activated_version_id": result.get("version_id"),
        "activated_trained_at": result.get("trained_at"),
    })


async def ml_delete_version_handler(request: Request) -> JSONResponse:
    """POST /api/v1/ml/delete-version — remove a non-active snapshot from disk."""
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

    body, err = _parse_ml_request_body(raw)
    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)

    symbol = _normalize_ml_symbol(body.get("symbol") or "")
    strategy = (body.get("strategy") or "").upper()
    model_version = str(
        body.get("model_version") or body.get("version_id") or body.get("trained_at") or ""
    ).strip()
    from app.services.bots.ml_model_artifacts import (
        delete_model_version,
        model_root_for,
        normalize_model_timeframe,
    )

    timeframe = normalize_model_timeframe(
        body.get("timeframe")
        or (body.get("config") if isinstance(body.get("config"), dict) else {}).get("timeframe")
    )
    if not symbol or not strategy:
        return JSONResponse({"ok": False, "error": "symbol and strategy required"}, status_code=400)
    if not model_version:
        return JSONResponse({"ok": False, "error": "model_version required"}, status_code=400)

    if model_root_for(strategy, symbol, timeframe) is None:
        return JSONResponse(
            {"ok": False, "error": f"unknown strategy {strategy}"},
            status_code=400,
        )

    try:
        result = await asyncio.to_thread(
            delete_model_version, strategy, symbol, model_version, timeframe,
        )
    except Exception as exc:
        logger.exception("delete-version failed for %s/%s", strategy, symbol)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    if not result.get("ok"):
        # 409 when active; 404 when missing
        err_msg = str(result.get("error") or "")
        status = 409 if "active version" in err_msg.lower() else 404
        return JSONResponse(result, status_code=status)

    # Refresh status payload so the UI can drop the row in one round-trip.
    root = model_root_for(strategy, symbol, timeframe)
    artifact = None
    if strategy == "ML_SIGNAL_BOOST":
        art = "model.joblib"
    else:
        from app.services.bots.ml_model_artifacts import STRATEGY_ARTIFACTS
        arts = STRATEGY_ARTIFACTS.get(strategy) or []
        art = next((a for a in arts if a.endswith(".onnx")), arts[0] if arts else None)
    if root and art and os.path.isfile(os.path.join(root, art)):
        artifact = art

    meta = {}
    meta_path = os.path.join(root, "metadata.json") if root else ""
    if meta_path and os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}

    enriched = _ml_status_enrich(root or "", meta if isinstance(meta, dict) else {}, artifact)
    return JSONResponse({
        "ok": True,
        **enriched,
        "deleted_version_id": result.get("deleted_version_id"),
        "deleted_trained_at": result.get("deleted_trained_at"),
    })


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


async def get_symbol_news_handler(request: Request) -> JSONResponse:
    symbol = str(request.path_params.get("symbol") or "").upper().strip()
    if not symbol:
        return JSONResponse({"ok": False, "error": "symbol is required"}, status_code=400)

    refresh = str(request.query_params.get("refresh", "true")).lower() in ("1", "true", "yes")
    try:
        limit = int(request.query_params.get("limit", "40"))
    except (TypeError, ValueError):
        limit = 40
    try:
        lookback_hours = float(request.query_params.get("lookback_hours", "72"))
    except (TypeError, ValueError):
        lookback_hours = 72.0

    sources_param = request.query_params.get("sources")
    sources = [s.strip() for s in sources_param.split(",") if s.strip()] if sources_param else None

    from app.services.altdata.news_provider import get_symbol_news_feed
    import asyncio

    try:
        feed = await asyncio.to_thread(
            get_symbol_news_feed,
            symbol,
            refresh=refresh,
            lookback_hours=lookback_hours,
            limit=max(1, min(limit, 100)),
            sources=sources,
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    return JSONResponse({"ok": True, "news": feed})


def _parse_json_body(raw: object) -> dict:
    """Normalize Starlette request.json() — tolerate double-encoded JSON strings."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _bot_record_from_detail(detail: dict | None) -> dict | None:
    """Normalize get_bot_detail() — returns nested {bot: {...}} or flat bot dict."""
    if not detail:
        return None
    bot = detail.get("bot")
    if isinstance(bot, dict):
        return bot
    if detail.get("id") or detail.get("symbol"):
        return detail
    return None


async def strategy_suggest_handler(request: Request) -> JSONResponse:
    bot_id = request.path_params.get("bot_id")
    if not bot_id:
        return JSONResponse({"ok": False, "error": "bot_id is required"}, status_code=400)
    try:
        body = _parse_json_body(await request.json())
    except json.JSONDecodeError:
        body = {}

    try:
        days = int(body.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    run_backtest = bool(body.get("run_backtest", True))
    use_llm = bool(body.get("use_llm", True))

    state: AppState = request.app.state.terminal
    backtester = state.backtester
    feed = getattr(state.oms, "feed", None) if state.oms else None

    recent_run = None
    if body.get("recent_results") and isinstance(body["recent_results"], dict):
        recent_run = {"results": body["recent_results"]}

    try:
        from app.services.bots.strategy_advisor import advise_bot_strategy

        result = await advise_bot_strategy(
            str(bot_id),
            backtester=backtester,
            feed=feed,
            days=max(7, min(days, 90)),
            run_backtest=run_backtest and backtester is not None and feed is not None,
            use_llm=use_llm,
            recent_run=recent_run,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    try:
        return JSONResponse({"ok": True, "advisor": result})
    except (TypeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": f"Response serialization failed: {exc}"}, status_code=500)


async def meta_label_status_handler(request: Request) -> JSONResponse:
    bot_id = request.path_params.get("bot_id")
    if not bot_id:
        return JSONResponse({"ok": False, "error": "bot_id is required"}, status_code=400)
    import asyncio
    from app.services.bots.meta_label_model import get_meta_label_status

    try:
        status = await asyncio.to_thread(get_meta_label_status, str(bot_id))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    state: AppState = request.app.state.terminal
    if state.bot_manager:
        detail = state.bot_manager.get_bot_detail(str(bot_id))
        bot = _bot_record_from_detail(detail)
        if bot:
            from app.services.bots.meta_label_operational import operational_status
            status["operational"] = operational_status(bot.get("config") or {})

    return JSONResponse({"ok": True, "meta_label": status})


async def meta_label_retrain_handler(request: Request) -> JSONResponse:
    bot_id = request.path_params.get("bot_id")
    if not bot_id:
        return JSONResponse({"ok": False, "error": "bot_id is required"}, status_code=400)
    import asyncio
    from app.services.bots.meta_label_model import train_meta_label_model

    try:
        body = _parse_json_body(await request.json())
    except json.JSONDecodeError:
        body = {}
    try:
        min_samples = int(body.get("min_samples", 0)) or None
    except (TypeError, ValueError):
        min_samples = None

    try:
        result = await asyncio.to_thread(
            train_meta_label_model,
            str(bot_id),
            min_samples=min_samples,
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    status = 200 if result.get("ok") else 400
    return JSONResponse({"ok": bool(result.get("ok")), "result": result}, status_code=status)


async def meta_label_walk_forward_handler(request: Request) -> JSONResponse:
    """POST /api/v1/backtest/meta-label-walk-forward — OOS GBM gate evaluation only."""
    import asyncio

    try:
        body = _parse_json_body(await request.json())
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    state: AppState = request.app.state.terminal
    backtester = state.backtester
    feed = getattr(state.oms, "feed", None) if state.oms else None

    bot_id = str(body.get("bot_id") or "").strip()
    symbol = str(body.get("symbol") or "").strip().upper()
    strategy = str(body.get("strategy") or "CHART_AGENT").strip().upper()
    config = dict(body.get("config") or {})

    if bot_id and state.bot_manager:
        detail = state.bot_manager.get_bot_detail(bot_id)
        bot = _bot_record_from_detail(detail)
        if not bot:
            return JSONResponse({"ok": False, "error": f"Bot {bot_id} not found"}, status_code=404)
        symbol = symbol or str(bot.get("symbol") or "").upper()
        strategy = strategy or str(bot.get("strategy") or "CHART_AGENT").upper()
        bot_cfg = dict(bot.get("config") or {})
        if not body.get("timeframe") and bot.get("timeframe"):
            bot_cfg.setdefault("timeframe", bot.get("timeframe"))
        config = {**bot_cfg, **config}
        config["backtest_bot_id"] = bot_id

    if not symbol:
        return JSONResponse({"ok": False, "error": "symbol or bot_id is required"}, status_code=400)

    try:
        days = int(body.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    try:
        rolling_folds = int(body.get("rolling_folds", body.get("meta_label_wf_folds", 2)))
    except (TypeError, ValueError):
        rolling_folds = 2
    try:
        train_pct = float(body.get("train_pct", body.get("meta_label_wf_train_pct", 70)))
    except (TypeError, ValueError):
        train_pct = 70.0
    min_train_samples = body.get("min_train_samples")
    if min_train_samples is not None:
        try:
            min_train_samples = int(min_train_samples)
        except (TypeError, ValueError):
            min_train_samples = None

    timeframe = body.get("timeframe") or config.get("timeframe")
    interval = body.get("interval")

    account_balance = None
    if state.bot_manager:
        account_balance = state.bot_manager.get_account_balance()

    from app.services.bots.meta_label_operational import run_meta_label_walk_forward_sync

    try:
        result = await asyncio.to_thread(
            run_meta_label_walk_forward_sync,
            backtester.run_backtest if backtester else None,
            feed,
            symbol=symbol,
            strategy=strategy,
            config=config,
            days=days,
            timeframe=timeframe,
            interval=interval,
            rolling_folds=rolling_folds,
            train_pct=train_pct,
            min_train_samples=min_train_samples,
            account_balance=account_balance,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    return JSONResponse({
        "ok": bool(result.get("ok")),
        "walk_forward": result,
        "symbol": symbol,
        "strategy": strategy,
    })


async def meta_label_operational_handler(request: Request) -> JSONResponse:
    """POST /api/v1/bots/{bot_id}/meta-label/operational — shadow / promote / rollback."""
    import asyncio

    bot_id = request.path_params.get("bot_id")
    if not bot_id:
        return JSONResponse({"ok": False, "error": "bot_id is required"}, status_code=400)

    try:
        body = _parse_json_body(await request.json())
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "Invalid JSON body"}, status_code=400)

    stage = str(body.get("stage") or "").strip().lower()
    if not stage:
        return JSONResponse({"ok": False, "error": "stage is required (shadow, promote, rollback)"}, status_code=400)

    state: AppState = request.app.state.terminal
    if not state.bot_manager:
        return JSONResponse({"ok": False, "error": "Bot manager not available"}, status_code=503)

    detail = state.bot_manager.get_bot_detail(str(bot_id))
    bot = _bot_record_from_detail(detail)
    if not bot:
        return JSONResponse({"ok": False, "error": f"Bot {bot_id} not found"}, status_code=404)

    if str(bot.get("strategy") or "").upper() != "CHART_AGENT":
        return JSONResponse(
            {"ok": False, "error": "Meta-label operational rollout requires CHART_AGENT"},
            status_code=400,
        )

    from app.services.bots.meta_label_operational import build_operational_patch, operational_status
    from app.services.bots.meta_label_model import train_meta_label_model

    walk_forward = body.get("walk_forward")
    if walk_forward is not None and not isinstance(walk_forward, dict):
        return JSONResponse({"ok": False, "error": "walk_forward must be an object"}, status_code=400)

    require_positive_oos = bool(body.get("require_positive_oos", True))
    retrain = bool(body.get("retrain", stage == "promote"))

    try:
        patch = build_operational_patch(
            stage,
            walk_forward=walk_forward,
            require_positive_oos=require_positive_oos,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    retrain_result = None
    if retrain and stage in ("shadow", "promote"):
        cfg = {**(bot.get("config") or {}), **patch}
        min_n = int(cfg.get("meta_label_min_train_samples") or 30)
        try:
            retrain_result = await asyncio.to_thread(
                train_meta_label_model,
                str(bot_id),
                min_samples=min_n,
            )
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"Retrain failed: {exc}"}, status_code=500)

        if stage == "promote" and not retrain_result.get("ok"):
            return JSONResponse({
                "ok": False,
                "error": retrain_result.get("error") or "Model not trained — need more closed trades",
                "retrain": retrain_result,
            }, status_code=400)

    try:
        updated = await state.bot_manager.update_bot_config(str(bot_id), patch)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    prior = operational_status(bot.get("config") or {})
    current = operational_status(updated.get("config") or patch)

    return JSONResponse({
        "ok": True,
        "stage": stage,
        "patch": patch,
        "prior": prior,
        "current": current,
        "retrain": retrain_result,
        "bot": updated,
    })


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
            "config_snapshot": result.get("config_snapshot") or {},
        })

    state: AppState = request.app.state.terminal
    try:
        detail = await state.bot_manager.update_bot_config(str(bot_id), patch)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # update_bot_config returns get_bot_detail() envelope {bot, position, ...}.
    bot = detail.get("bot") if isinstance(detail, dict) else None
    if not isinstance(bot, dict):
        bot = detail if isinstance(detail, dict) else {}
    cfg = bot.get("config") if isinstance(bot.get("config"), dict) else {}
    config_snapshot = {
        "min_confidence": cfg.get("min_confidence"),
        "min_score": cfg.get("min_score"),
        "block_elevated_vol": bool(cfg.get("block_elevated_vol")),
        "calibration_gate_enabled": bool(cfg.get("calibration_gate_enabled")),
    }

    return JSONResponse({
        "ok": True,
        "patch": patch,
        "applied": result.get("applied") or [],
        "message": result.get("message"),
        "config_snapshot": config_snapshot,
        "bot": bot,
        "detail": detail,
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


async def market_footprint_handler(request: Request) -> JSONResponse:
    from app.services.archive.query import query_footprint_detailed

    symbol = request.query_params.get("symbol")
    if not symbol:
        return JSONResponse({"ok": False, "error": "symbol is required"}, status_code=400)

    try:
        from_ts = int(request.query_params.get("from_ts", 0))
        to_ts = int(request.query_params.get("to_ts", 0))
        price_step = float(request.query_params.get("price_step", 0.0))
        time_bucket_ms = int(request.query_params.get("time_bucket_ms", 60000))
    except ValueError:
        return JSONResponse({"ok": False, "error": "invalid numeric parameters"}, status_code=400)

    if price_step <= 0 or time_bucket_ms <= 0:
        return JSONResponse(
            {"ok": False, "error": "price_step and time_bucket_ms must be > 0"},
            status_code=400,
        )

    import asyncio

    try:
        footprint, meta = await asyncio.to_thread(
            query_footprint_detailed,
            symbol.upper(),
            from_ts,
            to_ts,
            price_step,
            time_bucket_ms,
        )
        if meta.get("error"):
            return JSONResponse(
                {"ok": False, "error": meta["error"], "meta": meta},
                status_code=500,
            )
        body: dict = {"ok": True, "footprint": footprint, "meta": meta}
        if meta.get("range_note"):
            body["message"] = meta["range_note"]
        return JSONResponse(body)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def generate_daily_briefing_handler(request: Request) -> JSONResponse:
    from app.services.agent.briefing import generate_daily_briefing
    import asyncio
    
    state: AppState = request.app.state.terminal
    
    try:
        result = await generate_daily_briefing(state)
        if result.get("ok"):
            return JSONResponse({
                "ok": True,
                "briefing": result.get("briefing"),
                "stats": result.get("stats"),
            })
        else:
            # Return 200 so the frontend handles it gracefully without a loud console error
            return JSONResponse({"ok": False, "error": result.get("error", "Unknown error")})
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Briefing generation failed: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def copilot_chat_handler(request: Request) -> JSONResponse:
    from app.services.agent.copilot import handle_message
    
    state: AppState = request.app.state.terminal
    try:
        body = await request.json()
    except Exception:
        body = {}
        
    result = await handle_message(
        state,
        message=body.get("message", ""),
        session_id=body.get("session_id"),
        active_symbol=body.get("active_symbol")
    )
    return JSONResponse(result.to_dict())

async def copilot_confirm_handler(request: Request) -> JSONResponse:
    from app.services.agent.copilot import confirm_action, cancel_action
    
    state: AppState = request.app.state.terminal
    try:
        body = await request.json()
    except Exception:
        body = {}
        
    pending_id = body.get("pending_id")
    if not pending_id:
        return JSONResponse({"ok": False, "error": "Missing pending_id"}, status_code=400)
        
    if body.get("cancel"):
        res = cancel_action(pending_id)
    else:
        res = await confirm_action(state, pending_id)
        
    return JSONResponse(res)

async def copilot_history_handler(request: Request) -> JSONResponse:
    from app.services.agent.copilot_store import list_messages
    
    session_id = request.query_params.get("session_id")
    limit = request.query_params.get("limit", "40")
    if not session_id:
        return JSONResponse({"ok": False, "error": "Missing session_id"}, status_code=400)
        
    msgs = list_messages(session_id, limit=int(limit))
    return JSONResponse({"ok": True, "messages": msgs})

async def copilot_clear_handler(request: Request) -> JSONResponse:
    from app.services.agent.copilot_store import clear_session
    
    session_id = request.path_params.get("session_id")
    if not session_id:
        return JSONResponse({"ok": False, "error": "Missing session_id"}, status_code=400)
        
    cleared = clear_session(session_id)
    return JSONResponse({"ok": True, "cleared": cleared})


def _make_endpoint(binding):
    async def endpoint(request: Request):
        request.scope["http_binding"] = binding
        return await _binding_handler(request)
    return endpoint


def create_http_app(state: AppState) -> Starlette:
    starlette_routes = [
        Route("/health/live", health_live, methods=["GET"]),
        Route("/health/massive", health_massive, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/api/v1/admin/shutdown", admin_shutdown_handler, methods=["POST"]),
        Route("/api/v1/session", session_handler, methods=["GET"]),
        Route("/metrics", metrics, methods=["GET"]),
        Route("/api/v1/strategies", list_strategies, methods=["GET"]),
        Route("/api/v1/ml/model-status", ml_model_status, methods=["GET"]),
        Route("/api/v1/ml/train", ml_train_handler, methods=["POST"]),
        Route("/api/v1/ml/validate", ml_validate_handler, methods=["POST"]),
        Route("/api/v1/ml/retrain-status", ml_retrain_status_handler, methods=["GET"]),
        Route("/api/v1/ml/jobs", ml_list_jobs_handler, methods=["GET"]),
        Route("/api/v1/ml/jobs/{job_id}", ml_get_job_handler, methods=["GET"]),
        Route("/api/v1/ml/jobs/{job_id}/cancel", ml_cancel_job_handler, methods=["POST"]),
        Route("/api/v1/ml/runs", ml_list_runs_handler, methods=["GET"]),
        Route("/api/v1/ml/activate-version", ml_activate_version_handler, methods=["POST"]),
        Route("/api/v1/ml/delete-version", ml_delete_version_handler, methods=["POST"]),
        Route("/api/v1/agent/insights/{symbol}", list_agent_insights, methods=["GET"]),
        Route("/api/v1/news/{symbol}", get_symbol_news_handler, methods=["GET"]),
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
        Route("/api/v1/backtest/meta-label-walk-forward", meta_label_walk_forward_handler, methods=["POST"]),
        Route("/api/v1/bots/calibration", get_bot_calibration_handler, methods=["GET"]),
        Route("/api/v1/bots/calibration/apply", apply_calibration_suggestions_handler, methods=["POST"]),
        Route("/api/v1/bots/{bot_id}/strategy-suggest", strategy_suggest_handler, methods=["POST"]),
        Route("/api/v1/bots/{bot_id}/meta-label/status", meta_label_status_handler, methods=["GET"]),
        Route("/api/v1/bots/{bot_id}/meta-label/retrain", meta_label_retrain_handler, methods=["POST"]),
        Route("/api/v1/bots/{bot_id}/meta-label/operational", meta_label_operational_handler, methods=["POST"]),
        Route("/api/v1/bots/filter-rejects", get_filter_rejects_handler, methods=["GET"]),
        Route("/api/v1/agent/pipeline/scan-deploy", agent_pipeline_scan_deploy_handler, methods=["POST"]),
        Route("/api/v1/agent/pipeline/status", agent_pipeline_status_handler, methods=["GET"]),
        Route("/api/v1/routes", list_api_routes, methods=["GET"]),
        Route("/api/v1/openapi.json", openapi_json, methods=["GET"]),
        Route("/api/v1/workspaces", get_workspaces_handler, methods=["GET"]),
        Route("/api/v1/workspaces", save_workspace_handler, methods=["POST"]),
        Route("/api/v1/workspaces/{workspace_id}", delete_workspace_handler, methods=["DELETE"]),
        Route("/api/v1/market/footprint", market_footprint_handler, methods=["GET"]),
        Route("/api/v1/journal/briefing/generate", generate_daily_briefing_handler, methods=["POST"]),
        Route("/api/v1/copilot/chat", copilot_chat_handler, methods=["POST"]),
        Route("/api/v1/copilot/confirm", copilot_confirm_handler, methods=["POST"]),
        Route("/api/v1/copilot/history", copilot_history_handler, methods=["GET"]),
        Route("/api/v1/copilot/history/{session_id}", copilot_clear_handler, methods=["DELETE"]),
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
