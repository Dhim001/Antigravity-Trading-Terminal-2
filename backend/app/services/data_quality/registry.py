"""In-memory last-seen tick/bar timestamps per symbol (hot path for monitor)."""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_last_tick_ms: dict[str, int] = {}
_last_bar_time: dict[str, int] = {}
_last_bid: dict[str, float] = {}
_last_ask: dict[str, float] = {}
_last_spread_pct: dict[str, float] = {}


def note_tick(
    symbol: str,
    *,
    time_ms: int | None = None,
    bid: float | None = None,
    ask: float | None = None,
) -> None:
    if not symbol:
        return
    ts = int(time_ms if time_ms is not None else time.time() * 1000)
    with _lock:
        _last_tick_ms[symbol] = ts
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            _last_bid[symbol] = float(bid)
            _last_ask[symbol] = float(ask)
            mid = (float(bid) + float(ask)) / 2
            if mid > 0:
                _last_spread_pct[symbol] = (float(ask) - float(bid)) / mid * 100


def note_bar(symbol: str, bar_time: int) -> None:
    if not symbol or bar_time is None:
        return
    with _lock:
        _last_bar_time[symbol] = int(bar_time)


def snapshot() -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    with _lock:
        symbols = sorted(set(_last_tick_ms) | set(_last_bar_time))
        per_symbol: dict[str, dict[str, Any]] = {}
        for sym in symbols:
            tick_ms = _last_tick_ms.get(sym)
            bar_t = _last_bar_time.get(sym)
            stale_sec = (now_ms - tick_ms) / 1000.0 if tick_ms else None
            spread = _last_spread_pct.get(sym)
            per_symbol[sym] = {
                "last_tick_ms": tick_ms,
                "last_bar_time": bar_t,
                "stale_sec": round(stale_sec, 1) if stale_sec is not None else None,
                "spread_pct": round(spread, 4) if spread is not None else None,
                "bid": _last_bid.get(sym),
                "ask": _last_ask.get(sym),
            }
        return {"checked_at_ms": now_ms, "symbols": per_symbol}
