"""Candle sourcing for chart analyst (archive + live feed merge)."""

from __future__ import annotations

from app.config import BOT_MIN_CANDLES
from app.services.bots.candle_source import get_bot_candles


async def get_agent_candles(symbol: str, feed, *, min_bars: int | None = None) -> list[dict]:
    """Return OHLCV series with enough warm-up bars for analyst indicators."""
    return get_bot_candles(symbol, feed, min_bars=min_bars or BOT_MIN_CANDLES)
