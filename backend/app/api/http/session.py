"""Single-round-trip session snapshot for frontend bootstrap."""

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.state import AppState
from app.config import (
    AGENT_ENABLED,
    AGENT_LLM_ENABLED,
    AGENT_VISION_ENABLED,
    ALLOW_CUSTOM_STRATEGIES,
    ALLOW_LIVE_BOTS,
    ARCHIVE_BACKEND,
    ARCHIVE_PARQUET_ENABLED,
    ARCHIVE_TICKS_ENABLED,
    BOT_MIN_CANDLES,
    OPERATOR_MODE,
    SCANNER_ENABLED,
    TERMINAL_MODE,
    TERMINAL_ROLE,
)
from app.database import get_db_stats
from app.services.bots.backtest_job_store import get_active_backtest_job
from app.services.bots.strategy_catalog import list_strategy_catalog
from app.services.bots.execution_mode import execution_mode_label
from app.services.order_capabilities import get_order_capabilities
from app.services.runtime.system_state import get_safe_mode_info


async def session_handler(request: Request) -> JSONResponse:
    state: AppState = request.app.state.terminal

    llm_coro = asyncio.create_task(_safe_llm_status())
    stats_coro = asyncio.to_thread(get_db_stats)
    account_coro = asyncio.to_thread(state.oms.get_account_data)
    history_coro = asyncio.to_thread(state.oms.get_trade_history)
    llm, stats, account, history = await asyncio.gather(
        llm_coro,
        stats_coro,
        account_coro,
        history_coro,
        return_exceptions=True,
    )

    if isinstance(llm, Exception):
        llm = {"available": False, "provider": "off"}
    if isinstance(stats, Exception):
        stats = {}
    if isinstance(account, Exception):
        account = {"balances": {}, "positions": {}, "orders": []}
    if isinstance(history, Exception):
        history = []

    active_job = None
    resumable_backtest_jobs = []
    try:
        from app.services.bots.backtest_job_store import list_backtest_jobs

        active_job = get_active_backtest_job()
        recent = list_backtest_jobs(limit=20)
        resumable_backtest_jobs = [j for j in recent if j.get("resumable")]
    except Exception:
        try:
            active_job = get_active_backtest_job()
        except Exception:
            pass

    active_ml_jobs = []
    ml_queue = {"active": 0, "queued": 0}
    try:
        from app.services.bots.ml_job_store import list_ml_jobs, ml_job_counts, public_ml_job

        ml_queue = ml_job_counts()
        # Include resumable interrupted jobs so FE can reattach Auto-Tune / validate.
        recent_ml = list_ml_jobs(limit=20, active_only=False)
        active_ml_jobs = [
            public_ml_job(j, include_result=False)
            for j in recent_ml
            if j.get("status") in ("queued", "running")
            or j.get("resumable")
            or (
                isinstance(j.get("checkpoint"), dict)
                and j["checkpoint"].get("resume_ok")
                and j.get("status") in ("error", "cancelled")
            )
        ][:10]
    except Exception:
        pass

    return JSONResponse({
        "ok": True,
        "session": {
            "terminal": {
                "terminal_mode": TERMINAL_MODE,
                "terminal_role": TERMINAL_ROLE,
                "execution_mode": execution_mode_label(),
                "allow_live_bots": ALLOW_LIVE_BOTS,
                "allow_custom_strategies": ALLOW_CUSTOM_STRATEGIES,
                "archive_parquet_enabled": ARCHIVE_PARQUET_ENABLED,
                "archive_backend": ARCHIVE_BACKEND,
                "archive_ticks_enabled": ARCHIVE_TICKS_ENABLED,
                "bot_min_candles": BOT_MIN_CANDLES,
                "agent_llm_enabled": AGENT_LLM_ENABLED,
                "agent_vision_enabled": AGENT_VISION_ENABLED,
                "agent_enabled": AGENT_ENABLED,
                "scanner_enabled": SCANNER_ENABLED,
                "operator_mode": OPERATOR_MODE,
                "order_capabilities": get_order_capabilities(state.oms),
                "safe_mode": get_safe_mode_info(),
            },
            "llm": llm,
            "account": account,
            "history": history,
            "bots": state.bot_manager.list_bots_public(),
            "strategies": list_strategy_catalog(),
            "active_backtest_job": active_job,
            "resumable_backtest_jobs": resumable_backtest_jobs,
            "active_ml_jobs": active_ml_jobs,
            "ml_queue": ml_queue,
            "metrics": {
                "open_positions": stats.get("positions_count", 0),
                "pending_orders": stats.get("pending_orders_count", 0),
                "ml_jobs_active": ml_queue.get("active", 0),
                "ml_jobs_queued": ml_queue.get("queued", 0),
            },
        },
    })


async def _safe_llm_status() -> dict:
    from app.services.agent.llm.router import get_llm_status

    # Keep session bootstrap snappy — a slow Ollama probe must not stall the UI.
    try:
        return await asyncio.wait_for(get_llm_status(), timeout=0.75)
    except Exception:
        return {"available": False, "provider": "off"}
