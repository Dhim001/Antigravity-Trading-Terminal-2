"""Optional sub-minute tick capture (trade/quote snapshots)."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import (
    ARCHIVE_TICKS_ENABLED,
    ARCHIVE_TICK_FLUSH_INTERVAL,
    ARCHIVE_TICK_RETENTION_HOURS,
    TERMINAL_MODE,
)
from app.db.connection import get_connection

logger = logging.getLogger(__name__)

_buffer: list[dict[str, Any]] = []
_last_flush = 0.0


def record_tick(symbol: str, price: float, *, volume: float = 0.0, time_ms: int | None = None) -> None:
    """Buffer a tick for batched DB write."""
    from app.config import ARCHIVE_TICKS_ENABLED, TERMINAL_MODE

    if not ARCHIVE_TICKS_ENABLED or not symbol or price <= 0:
        return
    ts_ms = int(time_ms if time_ms is not None else time.time() * 1000)
    _buffer.append({
        "symbol": symbol,
        "time_ms": ts_ms,
        "price": float(price),
        "volume": float(volume or 0),
        "source": TERMINAL_MODE or "SIMULATED",
    })


def flush_ticks() -> int:
    """Upsert buffered ticks; purge rows older than retention window."""
    global _last_flush
    from app.config import ARCHIVE_TICKS_ENABLED, ARCHIVE_TICK_RETENTION_HOURS

    if not ARCHIVE_TICKS_ENABLED or not _buffer:
        return 0

    batch = _buffer[:]
    _buffer.clear()

    conn = get_connection()
    cursor = conn.cursor()
    try:
        from app.db.connection import is_postgres

        if is_postgres():
            sql = """
                INSERT INTO market_ticks (symbol, time_ms, price, volume, source)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (symbol, time_ms) DO UPDATE SET
                  price = excluded.price,
                  volume = excluded.volume,
                  source = excluded.source
            """
        else:
            sql = """
                INSERT OR REPLACE INTO market_ticks (symbol, time_ms, price, volume, source)
                VALUES (?, ?, ?, ?, ?)
            """
        cutoff_ms = int((time.time() - ARCHIVE_TICK_RETENTION_HOURS * 3600) * 1000)
        params = [
            (row["symbol"], row["time_ms"], row["price"], row["volume"], row["source"])
            for row in batch
        ]
        cursor.execute("DELETE FROM market_ticks WHERE time_ms < ?", (cutoff_ms,))
        cursor.executemany(sql, params)
        written = len(params)
        conn.commit()
    except Exception as exc:
        logger.error("Tick flush failed: %s", exc)
    finally:
        conn.close()

    _last_flush = time.time()
    return written


def maybe_flush_ticks() -> int:
    from app.config import ARCHIVE_TICKS_ENABLED, ARCHIVE_TICK_FLUSH_INTERVAL

    if not ARCHIVE_TICKS_ENABLED:
        return 0
    if time.time() - _last_flush < ARCHIVE_TICK_FLUSH_INTERVAL:
        return 0
    return flush_ticks()


def query_ticks(symbol: str, from_ms: int, to_ms: int, limit: int = 10000) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT symbol, time_ms, price, volume, source
            FROM market_ticks
            WHERE symbol = ? AND time_ms >= ? AND time_ms <= ?
            ORDER BY time_ms
            LIMIT ?
            """,
            (symbol, from_ms, to_ms, limit),
        )
        rows = cursor.fetchall()
        out = []
        for row in rows:
            if isinstance(row, dict):
                out.append({
                    "symbol": row["symbol"],
                    "time_ms": int(row["time_ms"]),
                    "price": float(row["price"]),
                    "volume": float(row.get("volume") or 0),
                    "source": row.get("source"),
                })
            else:
                out.append({
                    "symbol": row[0],
                    "time_ms": int(row[1]),
                    "price": float(row[2]),
                    "volume": float(row[3] or 0),
                    "source": row[4],
                })
        return out
    finally:
        conn.close()


async def tick_flush_loop():
    logger.info(
        "Tick flush loop started (interval=%.0fs, retention=%dh)",
        ARCHIVE_TICK_FLUSH_INTERVAL,
        ARCHIVE_TICK_RETENTION_HOURS,
    )
    import asyncio
    while True:
        try:
            maybe_flush_ticks()
        except Exception as exc:
            logger.error("Tick flush loop error: %s", exc)
        await asyncio.sleep(ARCHIVE_TICK_FLUSH_INTERVAL)
