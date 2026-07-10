"""Range reads for archived market history."""

from __future__ import annotations

import time
from typing import Any

from app.config import ARCHIVE_RETENTION_1M_DAYS
from app.db.connection import get_connection


def _row_to_bar(row) -> dict[str, Any]:
    if isinstance(row, dict):
        return {
            "time": int(row["time"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume") or 0),
        }
    return {
        "time": int(row[1]),
        "open": float(row[2]),
        "high": float(row[3]),
        "low": float(row[4]),
        "close": float(row[5]),
        "volume": float(row[6]),
    }


def _query_table(
    table: str,
    symbol: str,
    from_ts: int,
    to_ts: int,
    limit: int = 50000,
) -> list[dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT symbol, time, open, high, low, close, volume
            FROM {table}
            WHERE symbol = ? AND time >= ? AND time <= ?
            ORDER BY time
            LIMIT ?
            """,
            (symbol, from_ts, to_ts, limit),
        )
        return [_row_to_bar(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def query_1m(symbol: str, from_ts: int, to_ts: int) -> list[dict[str, Any]]:
    return _query_table("market_bars_1m", symbol, from_ts, to_ts)


def query_1h(symbol: str, from_ts: int, to_ts: int) -> list[dict[str, Any]]:
    return _query_table("market_bars_1h", symbol, from_ts, to_ts)


def query_market_history(
    symbol: str,
    from_ts: int | None = None,
    to_ts: int | None = None,
    interval: str = "auto",
) -> list[dict[str, Any]]:
    now = int(time.time())
    to_ts = int(to_ts if to_ts is not None else now)
    from_ts = int(from_ts if from_ts is not None else now - 86400 * 7)

    if from_ts > to_ts:
        from_ts, to_ts = to_ts, from_ts

    interval = (interval or "auto").lower()
    cutoff_1m = now - int(ARCHIVE_RETENTION_1M_DAYS * 86400)

    if interval == "1m":
        return query_1m(symbol, from_ts, to_ts)
    if interval == "1h":
        return query_1h(symbol, from_ts, to_ts)

    bars: list[dict[str, Any]] = []
    if to_ts > cutoff_1m:
        bars.extend(query_1m(symbol, max(from_ts, cutoff_1m), to_ts))
    if from_ts < cutoff_1m:
        bars.extend(query_1h(symbol, from_ts, min(to_ts, cutoff_1m - 60)))

    deduped: dict[int, dict[str, Any]] = {}
    for bar in bars:
        deduped[bar["time"]] = bar
    return [deduped[t] for t in sorted(deduped)]


def query_footprint(
    symbol: str,
    from_ts: int,
    to_ts: int,
    price_step: float,
    time_bucket_ms: int = 60000,
) -> list[dict[str, Any]]:
    """
    Aggregate market ticks into a volume footprint heatmap.
    Returns a list of dicts: { "time": int, "price": float, "volume": float }
    """
    if price_step <= 0 or time_bucket_ms <= 0:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # We use CAST(price / price_step AS INTEGER) * price_step to bucket prices
        # and (time_ms / time_bucket_ms) * time_bucket_ms to bucket time.
        cursor.execute(
            f"""
            SELECT 
                (time_ms / ?) * ? AS bucket_time,
                CAST(price / ? AS INTEGER) * ? AS bucket_price,
                SUM(volume) as total_volume
            FROM market_ticks
            WHERE symbol = ? AND time_ms >= ? AND time_ms <= ?
            GROUP BY bucket_time, bucket_price
            ORDER BY bucket_time, bucket_price
            """,
            (time_bucket_ms, time_bucket_ms, price_step, price_step, symbol, from_ts, to_ts),
        )
        return [
            {
                "time": int(row[0]),
                "price": float(row[1]),
                "volume": float(row[2]),
            }
            for row in cursor.fetchall()
        ]
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Footprint query failed: {e}")
        return []
    finally:
        conn.close()


def get_archive_stats() -> dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    stats: dict[str, Any] = {
        "bars_1m": 0,
        "bars_1h": 0,
        "oldest_1m": None,
        "oldest_1h": None,
        "newest_1m": None,
        "newest_1h": None,
        "backend": "db",
        "est_size_mb": 0.0,
    }
    try:
        for table, key in (("market_bars_1m", "1m"), ("market_bars_1h", "1h")):
            try:
                cursor.execute(f"SELECT COUNT(*), MIN(time), MAX(time) FROM {table}")
                row = cursor.fetchone()
                if row:
                    if isinstance(row, dict):
                        vals = list(row.values())
                    else:
                        vals = list(row)
                    stats[f"bars_{key}"] = int(vals[0] or 0)
                    stats[f"oldest_{key}"] = int(vals[1]) if vals[1] is not None else None
                    stats[f"newest_{key}"] = int(vals[2]) if vals[2] is not None else None
            except Exception:
                pass
        stats["est_size_mb"] = round(
            (stats["bars_1m"] * 80 + stats["bars_1h"] * 88) / (1024 * 1024),
            2,
        )
        try:
            from app.services.archive.ingestion import get_ingestion_summary
            stats["ingestion"] = get_ingestion_summary()
        except Exception:
            pass
    finally:
        conn.close()
    return stats
