"""Normalize IB bar objects into terminal candle dicts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def bar_epoch_seconds(bar) -> int:
    """Unix seconds for an IB BarData / RealTimeBar date field."""
    raw = getattr(bar, "date", None)
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        try:
            if len(raw) == 8 and raw.isdigit():
                dt = datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            return 0
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    return 0


def bar_to_candle(bar) -> dict[str, float | int]:
    epoch = bar_epoch_seconds(bar)
    if epoch:
        epoch = (epoch // 60) * 60
    return {
        "time": epoch,
        "open": float(getattr(bar, "open", 0) or 0),
        "high": float(getattr(bar, "high", 0) or 0),
        "low": float(getattr(bar, "low", 0) or 0),
        "close": float(getattr(bar, "close", 0) or 0),
        "volume": round(float(getattr(bar, "volume", 0) or 0), 4),
    }


def bars_to_candles(bars) -> list[dict[str, Any]]:
    out: list[dict] = []
    for bar in bars or []:
        candle = bar_to_candle(bar)
        if candle["time"]:
            out.append(candle)
    return out
