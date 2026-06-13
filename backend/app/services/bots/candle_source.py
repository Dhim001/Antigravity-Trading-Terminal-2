"""Merge live feed buffers with archive history for bot indicator warm-up."""

from __future__ import annotations

import time

from app.config import ARCHIVE_ENABLED, BOT_MIN_CANDLES
from app.services.archive.query import query_market_history
from app.services.archive.resolve import merge_candle_series


def get_bot_candles(
    symbol: str,
    feed,
    *,
    min_bars: int | None = None,
) -> list[dict]:
    """
    Return OHLCV series for bot evaluation.

    Uses the in-memory feed buffer when it has enough bars; otherwise prepends
    archived 1m history so indicators (e.g. 200-period) can warm up on live feeds.
    """
    min_bars = max(50, int(min_bars if min_bars is not None else BOT_MIN_CANDLES))
    live = feed.get_candles(symbol) or [] if feed is not None else []

    if len(live) >= min_bars or not ARCHIVE_ENABLED:
        return live

    now = int(time.time())
    deficit = min_bars - len(live)
    lookback_secs = max(7 * 86400, deficit * 60 + 3600)
    from_ts = now - lookback_secs
    if live:
        from_ts = min(from_ts, int(live[0]["time"]) - deficit * 60)

    archived = query_market_history(symbol, from_ts, now, interval="1m")
    merged = merge_candle_series(archived, live)
    if len(merged) > min_bars + 100:
        merged = merged[-(min_bars + 100) :]
    return merged
