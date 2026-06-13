"""Wire live feed bar-close events into the bot engine."""

from __future__ import annotations

import logging

from app.services.bots.candle_source import get_bot_candles

logger = logging.getLogger(__name__)


def register_live_bot_hooks(feed, bot_manager) -> None:
    """Register event-driven bar-close callbacks on feeds that support them."""
    if not hasattr(feed, "register_bar_close_callback"):
        return

    async def on_bar_close(symbol: str) -> None:
        candles = get_bot_candles(symbol, feed)
        if candles:
            await bot_manager.process_market_tick(symbol, candles)

    feed.register_bar_close_callback(on_bar_close)
    logger.info("Live bot bar-close hooks registered on feed")
