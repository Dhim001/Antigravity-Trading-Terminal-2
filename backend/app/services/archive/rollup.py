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


def _fetch_existing_1h_map(rows: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    if not rows:
        return {}
    pairs = list({(r["symbol"], int(r["time"])) for r in rows})
    conn = get_connection()
    cursor = conn.cursor()
    try:
        clause = " OR ".join(["(symbol = ? AND time = ?)"] * len(pairs))
        params: list[Any] = []
        for sym, t in pairs:
            params.extend([sym, t])
        cursor.execute(
            f"""
            SELECT symbol, time, open, high, low, close, volume, source, bar_count
            FROM market_bars_1h
            WHERE {clause}
            """,
            params,
        )
        out: dict[tuple[str, int], dict[str, Any]] = {}
        for row in cursor.fetchall():
            if isinstance(row, dict):
                key = (row["symbol"], int(row["time"]))
                out[key] = dict(row)
            else:
                key = (row[0], int(row[1]))
                out[key] = {
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
        return out
    finally:
        conn.close()


def _upsert_1h_rows(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    existing_map = _fetch_existing_1h_map(rows)
    merged_rows: list[dict[str, Any]] = []
    for row in rows:
        key = (row["symbol"], int(row["time"]))
        existing = existing_map.get(key)
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


def purge_expired_1m(cutoff_ts: int, *, batch_limit: int = 200_000) -> int:
    """Bulk-delete 1m bars older than retention cutoff (batched to limit lock time)."""
    conn = get_connection()
    cursor = conn.cursor()
    total = 0
    try:
        while True:
            if is_postgres():
                cursor.execute(
                    """
                    DELETE FROM market_bars_1m
                    WHERE ctid IN (
                        SELECT ctid FROM market_bars_1m
                        WHERE time < ?
                        LIMIT ?
                    )
                    """,
                    (cutoff_ts, batch_limit),
                )
            else:
                cursor.execute(
                    """
                    DELETE FROM market_bars_1m
                    WHERE rowid IN (
                        SELECT rowid FROM market_bars_1m
                        WHERE time < ?
                        LIMIT ?
                    )
                    """,
                    (cutoff_ts, batch_limit),
                )
            deleted = cursor.rowcount if cursor.rowcount is not None else 0
            conn.commit()
            total += max(0, deleted)
            if deleted < batch_limit:
                break
        return total
    finally:
        conn.close()


def checkpoint_wal() -> None:
    """Truncate SQLite WAL after large deletes so disk/RSS can shrink."""
    if is_postgres():
        return
    conn = get_connection()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as exc:
        logger.debug("WAL checkpoint skipped: %s", exc)
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

    bulk_deleted = 0
    # Fast path: purge any remaining 1m rows past retention (covers gaps rollup missed).
    try:
        bulk_deleted = purge_expired_1m(cutoff_1m)
        minutes_deleted += bulk_deleted
        if bulk_deleted:
            logger.info("Purged %d expired 1m bar(s) past retention", bulk_deleted)
    except Exception as exc:
        logger.warning("1m retention purge failed: %s", exc)

    try:
        hours_purged = purge_expired_1h(cutoff_1h)
    except Exception as exc:
        logger.warning("1h retention purge failed: %s", exc)
        hours_purged = 0

    if minutes_deleted or hours_purged:
        try:
            checkpoint_wal()
        except Exception:
            pass

    return {
        "enabled": True,
        "cutoff_1m": cutoff_1m,
        "cutoff_1h": cutoff_1h,
        "hours_written": hours_written,
        "minutes_deleted": minutes_deleted,
        "hours_purged": hours_purged,
        "bulk_1m_deleted": bulk_deleted,
    }
