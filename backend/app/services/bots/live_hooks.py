"""Wire live feed bar-close events into the bot engine and chart analyst."""

from __future__ import annotations

import logging

from app.config import AGENT_ENABLED
from app.services.bots.candle_source import get_bot_candles

logger = logging.getLogger(__name__)


def register_live_bot_hooks(
    feed,
    bot_manager,
    *,
    chart_analyst=None,
    manager=None,
) -> None:
    """Register event-driven bar-close callbacks on feeds that support them."""
    if not hasattr(feed, "register_bar_close_callback"):
        return

    async def on_bar_close(symbol: str) -> None:
        candles = get_bot_candles(symbol, feed)
        if candles:
            await bot_manager.process_market_tick(symbol, candles)

        if AGENT_ENABLED and chart_analyst is not None:
            symbols = chart_analyst.symbols_to_analyze(bot_manager, manager)
            if symbol in symbols:
                try:
                    await chart_analyst.analyze(symbol, candles=candles, broadcast=True)
                except Exception as exc:
                    logger.debug("Bar-close chart analyze failed for %s: %s", symbol, exc)

    feed.register_bar_close_callback(on_bar_close)
    logger.info("Live bot bar-close hooks registered on feed")
