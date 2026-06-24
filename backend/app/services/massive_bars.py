"""Normalize Massive (formerly Polygon.io) bar/trade payloads into terminal candles."""

from __future__ import annotations

from typing import Any

from app.services.market.timeframes import normalize_timeframe


def bar_epoch_seconds(start_ms: int | float | None) -> int:
    """Minute bucket (Unix seconds) from Massive aggregate start timestamp (ms)."""
    if start_ms is None:
        return 0
    return int(int(start_ms) // 1000 // 60 * 60)


def bar_start_unix_seconds(start_ms: int | float | None) -> int:
    """Bar open time (Unix seconds) from Massive aggregate start timestamp (ms)."""
    if start_ms is None:
        return 0
    return int(int(start_ms) // 1000)


def timeframe_to_massive_range(timeframe: str) -> tuple[int, str]:
    """Map chart timeframe to Massive REST aggs multiplier/timespan."""
    key = normalize_timeframe(timeframe)
    mapping: dict[str, tuple[int, str]] = {
        "1m": (1, "minute"),
        "5m": (5, "minute"),
        "15m": (15, "minute"),
        "1h": (1, "hour"),
        "4h": (4, "hour"),
        "1d": (1, "day"),
    }
    return mapping[key]


def agg_to_candle(msg: dict[str, Any]) -> dict[str, float | int]:
    """Minute aggregate (ev=AM or XA) -> terminal candle dict."""
    epoch = bar_epoch_seconds(msg.get("s") or msg.get("t"))
    return {
        "time": epoch,
        "open": float(msg.get("o") or 0),
        "high": float(msg.get("h") or 0),
        "low": float(msg.get("l") or 0),
        "close": float(msg.get("c") or 0),
        "volume": round(float(msg.get("v") or 0), 4),
    }


def crypto_agg_to_candle(msg: dict[str, Any]) -> dict[str, float | int]:
    """Crypto minute aggregate (ev=XA) — same OHLCV shape as AM."""
    return agg_to_candle(msg)


def rest_agg_to_candle(bar: dict[str, Any]) -> dict[str, float | int]:
    """REST v2 aggs result object -> terminal candle (1m bucket)."""
    return agg_to_candle(bar)


def rest_agg_to_candle_native(bar: dict[str, Any]) -> dict[str, float | int]:
    """REST v2 aggs result -> terminal candle (native bar start, any timeframe)."""
    epoch = bar_start_unix_seconds(bar.get("s") or bar.get("t"))
    return {
        "time": epoch,
        "open": float(bar.get("o") or 0),
        "high": float(bar.get("h") or 0),
        "low": float(bar.get("l") or 0),
        "close": float(bar.get("c") or 0),
        "volume": round(float(bar.get("v") or 0), 4),
    }


def aggs_to_candles(bars: list[dict[str, Any]]) -> list[dict[str, float | int]]:
    out: list[dict] = []
    for bar in bars or []:
        candle = rest_agg_to_candle(bar)
        if candle["time"]:
            out.append(candle)
    return out


def aggs_to_candles_native(bars: list[dict[str, Any]]) -> list[dict[str, float | int]]:
    out: list[dict] = []
    for bar in bars or []:
        candle = rest_agg_to_candle_native(bar)
        if candle["time"]:
            out.append(candle)
    return out
