"""Aggregate finer OHLCV bars into larger intervals (parity with ChartWidget bucketCandles)."""

from __future__ import annotations

from typing import Any

from app.services.market.timeframes import normalize_timeframe, timeframe_to_secs


def _to_unix_seconds(t: Any) -> int | None:
    if t is None:
        return None
    try:
        val = int(t)
    except (TypeError, ValueError):
        return None
    if val > 1_000_000_000_000:
        val //= 1000
    return val


def _bar_fields(bar: dict) -> dict[str, float] | None:
    try:
        return {
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": float(bar.get("volume") or 0),
        }
    except (KeyError, TypeError, ValueError):
        return None


def resample_candles(raw: list[dict], interval_secs: int) -> list[dict]:
    """
    Bucket OHLCV bars by interval_secs.

    Mirrors frontend bucketCandles(): open=first, high=max, low=min, close=last,
    volume=sum. Incomplete trailing buckets are included (same as the chart).
    """
    if interval_secs <= 0:
        raise ValueError("interval_secs must be positive")

    buckets: dict[int, dict] = {}

    for bar in raw or []:
        sec = _to_unix_seconds(bar.get("time"))
        if sec is None:
            continue
        fields = _bar_fields(bar)
        if fields is None:
            continue

        bucket_time = (sec // interval_secs) * interval_secs
        existing = buckets.get(bucket_time)
        if existing is None:
            buckets[bucket_time] = {
                "time": bucket_time,
                **fields,
            }
        else:
            existing["high"] = max(existing["high"], fields["high"])
            existing["low"] = min(existing["low"], fields["low"])
            existing["close"] = fields["close"]
            existing["volume"] += fields["volume"]

    return [buckets[t] for t in sorted(buckets)]


def resample_candles_for_timeframe(raw: list[dict], timeframe: str) -> list[dict]:
    """Resample raw candles to the given canonical or alias timeframe."""
    key = normalize_timeframe(timeframe)
    secs = timeframe_to_secs(key)
    return resample_candles(raw, secs)
