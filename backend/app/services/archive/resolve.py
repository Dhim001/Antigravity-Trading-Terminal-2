"""Merge live feed candles with archived history for backtests and chart loads."""

from __future__ import annotations

import time
from typing import Any

from app.config import ARCHIVE_ENABLED, ARCHIVE_RETENTION_1M_DAYS, BOT_MIN_CANDLES
from app.services.archive.query import query_market_history
from app.services.market.resample import resample_candles_for_timeframe
from app.services.market.timeframes import is_valid_timeframe, normalize_timeframe


def _align_time(t: int) -> int:
    return (int(t) // 60) * 60


def merge_candle_series(*series: list[dict]) -> list[dict]:
    """Merge multiple OHLCV lists; later series win on duplicate timestamps."""
    by_time: dict[int, dict] = {}
    for candles in series:
        if not candles:
            continue
        for bar in candles:
            if bar.get("time") is None:
                continue
            t = _align_time(bar["time"])
            by_time[t] = {
                "time": t,
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar.get("volume") or 0),
            }
    return [by_time[t] for t in sorted(by_time)]


def resolve_candles_for_range(
    symbol: str,
    feed,
    *,
    from_ts: int | None = None,
    to_ts: int | None = None,
    days: int | None = None,
    interval: str = "auto",
) -> tuple[list[dict], dict[str, Any]]:
    """
    Combine archived bars with the in-memory feed buffer for a time window.
    Returns (candles, metadata).
    """
    now = int(time.time())
    if days is not None:
        from_ts = now - int(days) * 86400
        to_ts = now
    else:
        to_ts = int(to_ts if to_ts is not None else now)
        from_ts = int(from_ts if from_ts is not None else now - 7 * 86400)

    if from_ts > to_ts:
        from_ts, to_ts = to_ts, from_ts

    live: list[dict] = []
    if feed is not None and hasattr(feed, "get_candles"):
        live = feed.get_candles(symbol) or []

    archived: list[dict] = []
    if ARCHIVE_ENABLED:
        archived = query_market_history(symbol, from_ts, to_ts, interval=interval)

    merged = merge_candle_series(archived, live)
    windowed = [b for b in merged if from_ts <= b["time"] <= to_ts]

    meta = {
        "symbol": symbol,
        "from": from_ts,
        "to": to_ts,
        "count": len(windowed),
        "live_bars": len(live),
        "archived_bars": len(archived),
        "interval": interval,
        "archive_enabled": ARCHIVE_ENABLED,
    }
    if windowed:
        meta["oldest"] = windowed[0]["time"]
        meta["newest"] = windowed[-1]["time"]

    return windowed, meta


def resolve_backtest_candles(
    symbol: str,
    feed,
    *,
    days: int = 7,
    interval: str | None = None,
    timeframe: str = "1m",
) -> tuple[list[dict], dict[str, Any]]:
    """
    Load historical OHLCV for backtests.

    Always sources 1m archive/live data, then resamples when timeframe is coarser than 1m.
    Non-1m backtests cap range to 1m archive retention so buckets stay accurate.
    """
    days = max(1, min(int(days), 365))
    if not is_valid_timeframe(timeframe):
        raise ValueError(f"Unsupported backtest timeframe: {timeframe}")

    tf = normalize_timeframe(timeframe)
    effective_days = days
    timeframe_note = None

    if tf != "1m" and days > ARCHIVE_RETENTION_1M_DAYS:
        effective_days = int(ARCHIVE_RETENTION_1M_DAYS)
        timeframe_note = (
            f"Range capped to {effective_days}d for {tf} resample (1m archive retention)"
        )

    if interval is None:
        interval = "1m" if effective_days <= ARCHIVE_RETENTION_1M_DAYS else "auto"

    if tf != "1m":
        interval = "1m"

    candles_1m, meta = resolve_candles_for_range(
        symbol, feed, days=effective_days, interval=interval
    )
    meta["days"] = days
    meta["effective_days"] = effective_days
    meta["timeframe"] = tf
    meta["interval"] = interval
    if timeframe_note:
        meta["timeframe_note"] = timeframe_note

    if tf == "1m":
        meta["resolution_note"] = (
            "1m bars"
            if interval == "1m"
            else "mixed 1m (recent) + 1h (older)"
        )
        return candles_1m, meta

    resampled = resample_candles_for_timeframe(candles_1m, tf)
    max_bars = max(50, BOT_MIN_CANDLES) + 100
    if len(resampled) > max_bars:
        resampled = resampled[-max_bars:]
    meta["bars_1m"] = len(candles_1m)
    meta["count"] = len(resampled)
    meta["resolution_note"] = f"1m bars resampled to {tf}"
    if resampled:
        meta["oldest"] = resampled[0]["time"]
        meta["newest"] = resampled[-1]["time"]
    return resampled, meta
