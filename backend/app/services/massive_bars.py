"""Normalize Massive (formerly Polygon.io) bar/trade payloads into terminal candles."""

from __future__ import annotations

from typing import Any


def bar_epoch_seconds(start_ms: int | float | None) -> int:
    """Minute bucket (Unix seconds) from Massive aggregate start timestamp (ms)."""
    if start_ms is None:
        return 0
    return int(int(start_ms) // 1000 // 60 * 60)


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
    """REST v2 aggs result object -> terminal candle."""
    return agg_to_candle(bar)


def aggs_to_candles(bars: list[dict[str, Any]]) -> list[dict[str, float | int]]:
    out: list[dict] = []
    for bar in bars or []:
        candle = rest_agg_to_candle(bar)
        if candle["time"]:
            out.append(candle)
    return out
