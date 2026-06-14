"""Rolling tick buffer for sub-minute strategy evaluation."""

from __future__ import annotations

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

    def record(self, symbol: str, price: float, time_ms: int) -> None:
        if not symbol or price <= 0:
            return
        buf = self._buffers.setdefault(symbol, deque(maxlen=self._max_ticks))
        buf.append((time_ms, price))

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
