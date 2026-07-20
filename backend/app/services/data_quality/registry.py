"""In-memory last-seen tick/bar timestamps per symbol (hot path for monitor)."""

from __future__ import annotations

import os
import threading
import time
from typing import Any

_lock = threading.Lock()
_last_tick_ms: dict[str, int] = {}
_last_bar_time: dict[str, int] = {}
_last_bid: dict[str, float] = {}
_last_ask: dict[str, float] = {}
_last_spread_pct: dict[str, float] = {}
_last_seen_ms: dict[str, int] = {}

# Evict symbols idle longer than this (MEMORY #15).
_IDLE_TTL_MS = int(float(os.environ.get("DATA_QUALITY_IDLE_TTL_HOURS", "6")) * 3600 * 1000)
_MAX_SYMBOLS = int(os.environ.get("DATA_QUALITY_MAX_SYMBOLS", "500"))


def _touch(symbol: str, now_ms: int | None = None) -> None:
    ts = int(now_ms if now_ms is not None else time.time() * 1000)
    _last_seen_ms[symbol] = ts


def _evict_idle_unlocked(now_ms: int | None = None) -> None:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    ttl = max(60_000, _IDLE_TTL_MS)
    stale = [s for s, seen in _last_seen_ms.items() if now - seen > ttl]
    for sym in stale:
        _drop_symbol_unlocked(sym)
    if len(_last_seen_ms) <= _MAX_SYMBOLS:
        return
    ordered = sorted(_last_seen_ms.items(), key=lambda kv: kv[1])
    excess = len(ordered) - _MAX_SYMBOLS
    for sym, _ in ordered[:excess]:
        _drop_symbol_unlocked(sym)


def _drop_symbol_unlocked(symbol: str) -> None:
    _last_tick_ms.pop(symbol, None)
    _last_bar_time.pop(symbol, None)
    _last_bid.pop(symbol, None)
    _last_ask.pop(symbol, None)
    _last_spread_pct.pop(symbol, None)
    _last_seen_ms.pop(symbol, None)


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
        _touch(symbol, ts)
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            _last_bid[symbol] = float(bid)
            _last_ask[symbol] = float(ask)
            mid = (float(bid) + float(ask)) / 2
            if mid > 0:
                _last_spread_pct[symbol] = (float(ask) - float(bid)) / mid * 100
        _evict_idle_unlocked(ts)


def note_bar(symbol: str, bar_time: int) -> None:
    if not symbol or bar_time is None:
        return
    with _lock:
        _last_bar_time[symbol] = int(bar_time)
        _touch(symbol)
        _evict_idle_unlocked()


def snapshot() -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    with _lock:
        _evict_idle_unlocked(now_ms)
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
