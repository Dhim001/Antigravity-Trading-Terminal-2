"""Benchmark price series for portfolio comparison (yfinance + feed-native)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import yfinance as yf

from app.services.synthetic_data import YF_SYMBOL_MAP

logger = logging.getLogger(__name__)

# In-memory cache: symbol -> (fetched_at, series)
_BENCH_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL_SEC = 3600

DEFAULT_BENCHMARKS = {
    "SPY": "SPY",
    "BTC": "BTC-USD",
}


def _yf_ticker(symbol: str) -> str:
    return YF_SYMBOL_MAP.get(symbol, symbol)


def _fetch_yfinance_closes(ticker: str, *, period: str = "3mo", interval: str = "1d") -> list[dict]:
    try:
        hist = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    except Exception as exc:
        logger.warning("yfinance fetch failed for %s: %s", ticker, exc)
        return []
    if hist is None or hist.empty:
        return []
    out = []
    for idx, row in hist.iterrows():
        ts = int(idx.to_pydatetime().replace(tzinfo=timezone.utc).timestamp())
        close = float(row["Close"])
        if close > 0:
            out.append({"time": ts, "close": round(close, 4)})
    return out


def _rebase_pct(series: list[dict]) -> list[dict]:
    if not series:
        return []
    base = series[0]["close"]
    if base <= 0:
        return []
    return [
        {"time": p["time"], "value": round(((p["close"] / base) - 1.0) * 100, 2)}
        for p in series
    ]


def get_benchmark_series(
    symbol: str,
    *,
    period: str = "3mo",
    feed=None,
) -> list[dict]:
    """Return rebased % change series for a benchmark symbol."""
    cache_key = f"{symbol}:{period}"
    cached = _BENCH_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _CACHE_TTL_SEC:
        return cached[1]

    yf_sym = DEFAULT_BENCHMARKS.get(symbol.upper(), _yf_ticker(symbol))
    closes = []

    # Prefer feed-native history when the symbol is in the active universe.
    if feed and hasattr(feed, "candles") and symbol in getattr(feed, "candles", {}):
        candles = feed.candles.get(symbol) or []
        if len(candles) > 5:
            closes = [
                {"time": int(c["time"]), "close": float(c["close"])}
                for c in candles
                if c.get("close")
            ]

    if len(closes) < 5:
        closes = _fetch_yfinance_closes(yf_sym, period=period)

    series = _rebase_pct(closes)
    _BENCH_CACHE[cache_key] = (now, series)
    return series


def get_benchmarks(
    symbols: list[str] | None = None,
    *,
    period: str = "3mo",
    feed=None,
) -> dict:
    syms = symbols or ["SPY", "BTC"]
    out = {}
    for sym in syms:
        out[sym] = get_benchmark_series(sym, period=period, feed=feed)
    return {"benchmarks": out, "period": period}
