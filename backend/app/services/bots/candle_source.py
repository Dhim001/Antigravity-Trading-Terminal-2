"""Merge live feed buffers with archive history for bot indicator warm-up."""

from __future__ import annotations

import time

from app.config import ARCHIVE_ENABLED, BOT_MIN_CANDLES
from app.services.archive.query import query_market_history
from app.services.archive.resolve import merge_candle_series
from app.services.market.resample import resample_candles_for_timeframe
from app.services.market.timeframes import normalize_timeframe, timeframe_to_secs

_BASE_INTERVAL_SECS = 60


def _min_1m_bars_for_timeframe(timeframe: str, min_bars: int) -> int:
    """How many 1m bars to load so resampling yields enough TF bars."""
    key = normalize_timeframe(timeframe)
    interval_secs = timeframe_to_secs(key)
    factor = max(1, interval_secs // _BASE_INTERVAL_SECS)
    return min_bars * factor + factor


def _fetch_raw_1m(
    symbol: str,
    feed,
    *,
    min_bars: int,
) -> list[dict]:
    min_bars = max(50, int(min_bars))
    live = feed.get_candles(symbol) or [] if feed is not None else []

    if len(live) >= min_bars or not ARCHIVE_ENABLED:
        return live

    now = int(time.time())
    deficit = min_bars - len(live)
    lookback_secs = max(7 * 86400, deficit * _BASE_INTERVAL_SECS + 3600)
    from_ts = now - lookback_secs
    if live:
        from_ts = min(from_ts, int(live[0]["time"]) - deficit * _BASE_INTERVAL_SECS)

    archived = query_market_history(symbol, from_ts, now, interval="1m")
    merged = merge_candle_series(archived, live)
    if len(merged) > min_bars + 100:
        merged = merged[-(min_bars + 100) :]
    return merged


def candles_for_timeframe(
    raw_1m: list[dict],
    timeframe: str,
    *,
    min_bars: int | None = None,
) -> list[dict]:
    """Resample an in-memory 1m series to the bot timeframe (no archive merge)."""
    key = normalize_timeframe(timeframe)
    min_bars = max(50, int(min_bars if min_bars is not None else BOT_MIN_CANDLES))
    if key == "1m":
        out = list(raw_1m or [])
    else:
        out = resample_candles_for_timeframe(raw_1m or [], key)
    if len(out) > min_bars + 100:
        out = out[-(min_bars + 100) :]
    return out


def get_bot_candles(
    symbol: str,
    feed,
    *,
    timeframe: str = "1m",
    min_bars: int | None = None,
) -> list[dict]:
    """
    Return OHLCV series for bot evaluation at the requested timeframe.

    Always sources 1m bars from the feed buffer and archive, then resamples when
    timeframe is coarser than 1m.
    """
    min_bars = max(50, int(min_bars if min_bars is not None else BOT_MIN_CANDLES))
    key = normalize_timeframe(timeframe)
    min_1m = _min_1m_bars_for_timeframe(key, min_bars)
    raw = _fetch_raw_1m(symbol, feed, min_bars=min_1m)

    if key == "1m":
        if len(raw) > min_bars + 100:
            raw = raw[-(min_bars + 100) :]
        return raw

    resampled = resample_candles_for_timeframe(raw, key)
    if len(resampled) > min_bars + 100:
        resampled = resampled[-(min_bars + 100) :]
    return resampled
