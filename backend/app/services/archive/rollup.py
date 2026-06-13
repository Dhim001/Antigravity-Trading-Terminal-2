"""1m → 1h rollup and retention cleanup."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from app.config import (
    ARCHIVE_ENABLED,
    ARCHIVE_RETENTION_1H_DAYS,
    ARCHIVE_RETENTION_1M_DAYS,
)
from app.db.connection import get_connection, is_postgres

logger = logging.getLogger(__name__)

BATCH_SIZE = 5000


def _hour_bucket(t: int) -> int:
    return (int(t) // 3600) * 3600


def _aggregate_hour(bars: list[dict[str, Any]]) -> dict[str, Any]:
    bars = sorted(bars, key=lambda b: b["time"])
    return {
        "time": _hour_bucket(bars[0]["time"]),
        "open": bars[0]["open"],
        "high": max(b["high"] for b in bars),
        "low": min(b["low"] for b in bars),
        "close": bars[-1]["close"],
        "volume": sum(float(b.get("volume") or 0) for b in bars),
        "source": bars[-1]["source"],
        "bar_count": len(bars),
        "symbol": bars[0]["symbol"],
    }


def _merge_hour(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": incoming["symbol"],
        "time": incoming["time"],
        "open": existing["open"],
        "high": max(existing["high"], incoming["high"]),
        "low": min(existing["low"], incoming["low"]),
        "close": incoming["close"],
        "volume": float(existing.get("volume") or 0) + float(incoming.get("volume") or 0),
        "source": incoming["source"],
        "bar_count": int(existing.get("bar_count") or 0) + int(incoming.get("bar_count") or 0),
    }


def _fetch_existing_1h(symbol: str, hour_time: int) -> dict[str, Any] | None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT symbol, time, open, high, low, close, volume, source, bar_count
            FROM market_bars_1h
            WHERE symbol = ? AND time = ?
            """,
            (symbol, hour_time),
        )
        row = cursor.fetchone()
        if not row:
            return None
        if isinstance(row, dict):
            return dict(row)
        return {
            "symbol": row[0],
            "time": row[1],
            "open": row[2],
            "high": row[3],
            "low": row[4],
            "close": row[5],
            "volume": row[6],
            "source": row[7],
            "bar_count": row[8],
        }
    finally:
        conn.close()


def _upsert_1h_rows(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    merged_rows: list[dict[str, Any]] = []
    for row in rows:
        existing = _fetch_existing_1h(row["symbol"], row["time"])
        merged_rows.append(_merge_hour(existing, row) if existing else row)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        params = [
            (
                r["symbol"],
                r["time"],
                r["open"],
                r["high"],
                r["low"],
                r["close"],
                r["volume"],
                r["source"],
                r["bar_count"],
            )
            for r in merged_rows
        ]
        if is_postgres():
            cursor.executemany(
                """
                INSERT INTO market_bars_1h
                    (symbol, time, open, high, low, close, volume, source, bar_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (symbol, time) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    source = EXCLUDED.source,
                    bar_count = EXCLUDED.bar_count
                """,
                params,
            )
        else:
            cursor.executemany(
                """
                INSERT OR REPLACE INTO market_bars_1h
                    (symbol, time, open, high, low, close, volume, source, bar_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
        conn.commit()
        return len(merged_rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rollup_symbol(symbol: str, cutoff_ts: int) -> tuple[int, int]:
    """Roll 1m bars older than cutoff into 1h. Returns (hours_written, minutes_deleted)."""
    conn = get_connection()
    cursor = conn.cursor()
    total_hours = 0
    total_deleted = 0

    try:
        while True:
            cursor.execute(
                """
                SELECT symbol, time, open, high, low, close, volume, source
                FROM market_bars_1m
                WHERE symbol = ? AND time < ?
                ORDER BY time
                LIMIT ?
                """,
                (symbol, cutoff_ts, BATCH_SIZE),
            )
            rows = cursor.fetchall()
            if not rows:
                break

            parsed = []
            for row in rows:
                if isinstance(row, dict):
                    parsed.append(dict(row))
                else:
                    parsed.append({
                        "symbol": row[0],
                        "time": row[1],
                        "open": row[2],
                        "high": row[3],
                        "low": row[4],
                        "close": row[5],
                        "volume": row[6],
                        "source": row[7],
                    })

            by_hour: dict[int, list[dict]] = defaultdict(list)
            for bar in parsed:
                by_hour[_hour_bucket(bar["time"])].append(bar)

            hour_rows = [_aggregate_hour(bars) for bars in by_hour.values()]
            _upsert_1h_rows(hour_rows)
            total_hours += len(hour_rows)

            times = [b["time"] for b in parsed]
            cursor.execute(
                f"""
                DELETE FROM market_bars_1m
                WHERE symbol = ? AND time IN ({",".join("?" * len(times))})
                """,
                (symbol, *times),
            )
            conn.commit()
            total_deleted += len(times)

            if len(rows) < BATCH_SIZE:
                break
    finally:
        conn.close()

    return total_hours, total_deleted


def purge_expired_1h(cutoff_ts: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM market_bars_1h WHERE time < ?", (cutoff_ts,))
        deleted = cursor.rowcount if cursor.rowcount is not None else 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def run_rollup_job(symbols: list[str]) -> dict[str, Any]:
    if not ARCHIVE_ENABLED:
        return {"enabled": False}

    now = int(time.time())
    cutoff_1m = now - int(ARCHIVE_RETENTION_1M_DAYS * 86400)
    cutoff_1h = now - int(ARCHIVE_RETENTION_1H_DAYS * 86400)

    hours_written = 0
    minutes_deleted = 0
    for symbol in symbols:
        try:
            h, m = rollup_symbol(symbol, cutoff_1m)
            hours_written += h
            minutes_deleted += m
        except Exception as exc:
            logger.warning("Rollup failed for %s: %s", symbol, exc)

    try:
        hours_purged = purge_expired_1h(cutoff_1h)
    except Exception as exc:
        logger.warning("1h retention purge failed: %s", exc)
        hours_purged = 0

    return {
        "enabled": True,
        "cutoff_1m": cutoff_1m,
        "cutoff_1h": cutoff_1h,
        "hours_written": hours_written,
        "minutes_deleted": minutes_deleted,
        "hours_purged": hours_purged,
    }
