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


def _period_to_lookback_days(period: str) -> int:
    raw = str(period or "3mo").strip().lower()
    if raw.endswith("mo"):
        try:
            return max(30, int(raw[:-2]) * 31)
        except ValueError:
            return 90
    if raw.endswith("y"):
        try:
            return max(90, int(raw[:-1]) * 365)
        except ValueError:
            return 365
    if raw.endswith("d"):
        try:
            return max(5, int(raw[:-1]))
        except ValueError:
            return 90
    return 90


def _fetch_alpaca_daily_closes(symbol: str, *, period: str = "3mo") -> list[dict]:
    try:
        from app.services.archive.broker_fetch import fetch_alpaca_tf_candles
    except Exception:
        return []
    # Map portfolio shorthand (BTC) → terminal crypto symbol.
    terminal = symbol.upper()
    if terminal == "BTC":
        terminal = "BTCUSDT"
    to_ts = int(time.time())
    from_ts = to_ts - _period_to_lookback_days(period) * 86400
    bars = fetch_alpaca_tf_candles(terminal, from_ts, to_ts, "1d") or []
    return [
        {"time": int(b["time"]), "close": float(b["close"])}
        for b in bars
        if b.get("close") and b.get("time")
    ]


def get_benchmark_series(
    symbol: str,
    *,
    period: str = "3mo",
    feed=None,
) -> list[dict]:
    """Return rebased % change series for a benchmark symbol."""
    from app.config import TERMINAL_MODE

    cache_key = f"{symbol}:{period}:{TERMINAL_MODE}"
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

    if len(closes) < 5 and TERMINAL_MODE == "LIVE_ALPACA":
        closes = _fetch_alpaca_daily_closes(symbol, period=period)

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
