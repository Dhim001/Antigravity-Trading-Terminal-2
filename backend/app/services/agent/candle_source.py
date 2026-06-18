"""Candle sourcing for chart analyst (archive + live feed merge)."""

from __future__ import annotations

from app.services.bots.candle_source import get_bot_candles


async def get_agent_candles(
    symbol: str,
    feed,
    *,
    timeframe: str = "1m",
    min_bars: int | None = None,
) -> list[dict]:
    """Return OHLCV series at the requested timeframe for analyst indicators."""
    return get_bot_candles(symbol, feed, timeframe=timeframe, min_bars=min_bars)
