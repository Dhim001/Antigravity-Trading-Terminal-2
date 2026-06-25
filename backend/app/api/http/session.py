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
    SCANNER_ENABLED,
    TERMINAL_MODE,
    TERMINAL_ROLE,
)
from app.database import get_db_stats
from app.services.bots.backtest_job_store import get_active_backtest_job
from app.services.bots.strategy_catalog import list_strategy_catalog
from app.services.bots.execution_mode import execution_mode_label


async def session_handler(request: Request) -> JSONResponse:
    state: AppState = request.app.state.terminal

    llm_coro = asyncio.create_task(_safe_llm_status())
    stats_coro = asyncio.to_thread(get_db_stats)
    llm, stats = await asyncio.gather(llm_coro, stats_coro, return_exceptions=True)

    if isinstance(llm, Exception):
        llm = {"available": False, "provider": "off"}
    if isinstance(stats, Exception):
        stats = {}

    active_job = None
    try:
        active_job = get_active_backtest_job()
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
            },
            "llm": llm,
            "account": state.oms.get_account_data(),
            "history": state.oms.get_trade_history(),
            "bots": state.bot_manager.list_bots_public(),
            "strategies": list_strategy_catalog(),
            "active_backtest_job": active_job,
            "metrics": {
                "open_positions": stats.get("positions_count", 0),
                "pending_orders": stats.get("pending_orders_count", 0),
            },
        },
    })


async def _safe_llm_status() -> dict:
    from app.services.agent.llm.router import get_llm_status

    return await get_llm_status()
