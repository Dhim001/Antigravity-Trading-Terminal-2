"""Shared paper OMS maintenance — limit fills and SL/TP (Sim + LIVE_MASSIVE)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.api.outbound import publish_bot_log, publish_post_trade_bundle
from app.db.async_bridge import run_db

logger = logging.getLogger(__name__)


def _paper_oms_sync_tick(oms: Any) -> tuple[Any, Any, list, list]:
    """Sync DB + feed work — must not run on the asyncio event loop."""
    fills = oms.match_pending_orders()
    sl_tp_fills, sl_tp_logs, bot_exits = oms.check_sl_tp_triggers()
    return fills, sl_tp_fills, sl_tp_logs, bot_exits


async def run_paper_oms_tick(
    oms: Any,
    bot_manager: Any,
    manager: Any,
) -> bool:
    """
    Match pending limits and evaluate SL/TP against current feed prices.

    Returns True when any fill occurred (limit or SL/TP).
    """
    fills, sl_tp_fills, sl_tp_logs, bot_exits = await run_db(_paper_oms_sync_tick, oms)

    if sl_tp_logs:
        for log_msg in sl_tp_logs:
            logger.info(log_msg)
            await publish_bot_log(manager.broadcast, "system", "INFO", log_msg)

    if bot_exits:
        await bot_manager.handle_sl_tp_exits(bot_exits)

    total_fills = fills + sl_tp_fills
    if total_fills:
        logger.info("Paper OMS fills: %s", total_fills)
        await publish_post_trade_bundle(
            manager.broadcast,
            oms.get_account_data(),
            oms.get_trade_history(),
        )
        return True
    return False
