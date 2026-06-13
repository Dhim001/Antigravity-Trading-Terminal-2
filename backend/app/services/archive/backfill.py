"""One-time / on-demand backfill of market_bars_1m from seed parquet or live feed buffer."""

from __future__ import annotations

import logging
import os
from typing import Any

from app.config import ARCHIVE_ENABLED, SYMBOLS
from app.db.connection import get_connection
from app.services.archive.writer import _upsert_1m_rows, align_bar_time

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SEED_SOURCE = "SEED"


def symbol_has_archive(symbol: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT 1 FROM market_bars_1m WHERE symbol = ? LIMIT 1",
            (symbol,),
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


def _dataframe_to_bars(symbol: str, df) -> list[dict[str, Any]]:
    import pandas as pd

    bars: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        ts = pd.Timestamp(idx)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        t = align_bar_time(int(ts.timestamp()))
        bars.append({
            "symbol": symbol,
            "time": t,
            "open": round(float(row["Open"]), 6),
            "high": round(float(row["High"]), 6),
            "low": round(float(row["Low"]), 6),
            "close": round(float(row["Close"]), 6),
            "volume": round(float(row.get("Volume", 0) or 0), 2),
            "source": SEED_SOURCE,
        })
    return bars


def backfill_symbol_from_parquet(symbol: str) -> int:
    """Import 7d seed parquet into market_bars_1m. Returns rows written."""
    path = os.path.join(DATA_DIR, f"{symbol}_7d_1m.parquet")
    if not os.path.isfile(path):
        return 0

    try:
        import pandas as pd

        df = pd.read_parquet(path)
        if df.empty:
            return 0
        bars = _dataframe_to_bars(symbol, df)
        if not bars:
            return 0
        return _upsert_1m_rows(bars)
    except Exception as exc:
        logger.warning("Parquet backfill failed for %s: %s", symbol, exc)
        return 0


def backfill_symbol_from_feed(feed, symbol: str, source: str) -> int:
    """Upsert in-memory feed candles into market_bars_1m."""
    if feed is None or not hasattr(feed, "get_candles"):
        return 0
    candles = feed.get_candles(symbol)
    if not candles:
        return 0
    rows = []
    for bar in candles:
        if bar.get("time") is None:
            continue
        t = align_bar_time(bar["time"])
        rows.append({
            "symbol": symbol,
            "time": t,
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": float(bar.get("volume") or 0),
            "source": source,
        })
    if not rows:
        return 0
    return _upsert_1m_rows(rows)


def run_archive_backfill(
    symbols: list[str] | None = None,
    feed=None,
    source: str = "SIMULATED",
    *,
    skip_existing: bool = True,
    include_parquet: bool = True,
    include_feed: bool = True,
) -> dict[str, Any]:
    """
    Populate market_bars_1m from seed parquet files and/or live feed buffer.
    Option A storage only — writes to SQLite/Postgres via get_connection().
    """
    if not ARCHIVE_ENABLED:
        return {"enabled": False, "symbols": 0, "rows_written": 0}

    syms = symbols or list(SYMBOLS.keys())
    total = 0
    details: dict[str, dict[str, int]] = {}

    for symbol in syms:
        if skip_existing and symbol_has_archive(symbol):
            details[symbol] = {"skipped": 1, "parquet": 0, "feed": 0}
            continue

        parquet_rows = backfill_symbol_from_parquet(symbol) if include_parquet else 0
        feed_rows = 0
        if include_feed and parquet_rows == 0:
            feed_rows = backfill_symbol_from_feed(feed, symbol, source)

        written = parquet_rows + feed_rows
        total += written
        details[symbol] = {"parquet": parquet_rows, "feed": feed_rows, "total": written}

    result = {
        "enabled": True,
        "symbols": len(syms),
        "rows_written": total,
        "details": details,
    }
    if total:
        logger.info("Archive DB backfill complete: %d rows across %d symbols", total, len(syms))
    return result
