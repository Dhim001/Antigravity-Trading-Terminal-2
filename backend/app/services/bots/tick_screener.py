"""Rolling tick buffer for sub-minute strategy evaluation."""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class TickContext:
    symbol: str
    price: float
    time_ms: int
    prices: list[float]
    returns_pct: list[float]
    vwap: float
    zscore: float
    momentum_pct: float


class TickScreener:
    def __init__(self, max_ticks: int = 200):
        self._max_ticks = max(20, max_ticks)
        self._buffers: dict[str, deque[tuple[int, float]]] = {}
        self._last_seen: dict[str, float] = {}
        self._idle_ttl_sec = float(os.environ.get("TICK_SCREENER_IDLE_TTL_HOURS", "6")) * 3600
        self._max_symbols = int(os.environ.get("TICK_SCREENER_MAX_SYMBOLS", "500"))

    def _evict_idle(self) -> None:
        now = time.time()
        ttl = max(60.0, self._idle_ttl_sec)
        stale = [s for s, seen in self._last_seen.items() if now - seen > ttl]
        for sym in stale:
            self._buffers.pop(sym, None)
            self._last_seen.pop(sym, None)
        if len(self._buffers) <= self._max_symbols:
            return
        ordered = sorted(self._last_seen.items(), key=lambda kv: kv[1])
        excess = len(ordered) - self._max_symbols
        for sym, _ in ordered[:excess]:
            self._buffers.pop(sym, None)
            self._last_seen.pop(sym, None)

    def record(self, symbol: str, price: float, time_ms: int) -> None:
        if not symbol or price <= 0:
            return
        buf = self._buffers.setdefault(symbol, deque(maxlen=self._max_ticks))
        buf.append((time_ms, price))
        self._last_seen[symbol] = time.time()
        if len(self._buffers) > self._max_symbols or len(self._buffers) % 32 == 0:
            self._evict_idle()

    def context(self, symbol: str, price: float, time_ms: int, lookback: int) -> TickContext | None:
        buf = self._buffers.get(symbol)
        if not buf or len(buf) < 5:
            return None

        lookback = max(5, min(lookback, len(buf)))
        window = list(buf)[-lookback:]
        prices = [p for _, p in window]
        if price > 0:
            prices[-1] = price

        returns: list[float] = []
        for i in range(1, len(prices)):
            prev = prices[i - 1]
            if prev > 0:
                returns.append((prices[i] - prev) / prev * 100.0)

        mean = sum(prices) / len(prices)
        var = sum((p - mean) ** 2 for p in prices) / len(prices)
        std = var ** 0.5
        zscore = (price - mean) / std if std > 1e-12 else 0.0

        start = prices[0]
        momentum_pct = ((price - start) / start * 100.0) if start > 0 else 0.0
        vwap = mean

        return TickContext(
            symbol=symbol,
            price=price,
            time_ms=time_ms,
            prices=prices,
            returns_pct=returns,
            vwap=vwap,
            zscore=zscore,
            momentum_pct=momentum_pct,
        )
