"""Optional sub-minute tick capture (trade/quote snapshots)."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import (
    ARCHIVE_TICK_BATCH_MAX,
    ARCHIVE_TICK_FLUSH_INTERVAL,
    ARCHIVE_TICK_RETENTION_HOURS,
    TERMINAL_MODE,
)
from app.db.connection import get_connection
from app.services.data_quality import registry

logger = logging.getLogger(__name__)

_buffer: list[dict[str, Any]] = []
_last_flush = 0.0


def record_tick(
    symbol: str,
    price: float,
    *,
    volume: float = 0.0,
    time_ms: int | None = None,
    bid: float | None = None,
    ask: float | None = None,
    tick_type: str = "trade",
) -> None:
    """Update data-quality registry; optionally buffer ticks for DB write."""
    if not symbol:
        return
    ts_ms = int(time_ms if time_ms is not None else time.time() * 1000)
    registry.note_tick(symbol, time_ms=ts_ms, bid=bid, ask=ask)

    from app.config import ARCHIVE_TICKS_ENABLED

    if not ARCHIVE_TICKS_ENABLED or price <= 0:
        return

    spread = None
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        mid = (float(bid) + float(ask)) / 2
        if mid > 0:
            spread = (float(ask) - float(bid)) / mid * 100

    if len(_buffer) >= ARCHIVE_TICK_BATCH_MAX:
        flush_ticks()

    _buffer.append({
        "symbol": symbol,
        "time_ms": ts_ms,
        "price": float(price),
        "volume": float(volume or 0),
        "source": TERMINAL_MODE or "SIMULATED",
        "bid": float(bid) if bid is not None else None,
        "ask": float(ask) if ask is not None else None,
        "spread": spread,
        "tick_type": tick_type or "trade",
    })


def flush_ticks() -> int:
    """Upsert buffered ticks; purge rows older than retention window."""
    global _last_flush
    from app.config import ARCHIVE_TICKS_ENABLED

    if not ARCHIVE_TICKS_ENABLED or not _buffer:
        return 0

    batch = _buffer[:]
    _buffer.clear()

    conn = get_connection()
    cursor = conn.cursor()
    written = 0
    try:
        from app.db.connection import is_postgres

        if is_postgres():
            sql = """
                INSERT INTO market_ticks (
                    symbol, time_ms, price, volume, source, bid, ask, spread, tick_type
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (symbol, time_ms) DO UPDATE SET
                  price = excluded.price,
                  volume = excluded.volume,
                  source = excluded.source,
                  bid = excluded.bid,
                  ask = excluded.ask,
                  spread = excluded.spread,
                  tick_type = excluded.tick_type
            """
        else:
            sql = """
                INSERT OR REPLACE INTO market_ticks (
                    symbol, time_ms, price, volume, source, bid, ask, spread, tick_type
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        cutoff_ms = int((time.time() - ARCHIVE_TICK_RETENTION_HOURS * 3600) * 1000)
        params = [
            (
                row["symbol"],
                row["time_ms"],
                row["price"],
                row["volume"],
                row["source"],
                row.get("bid"),
                row.get("ask"),
                row.get("spread"),
                row.get("tick_type") or "trade",
            )
            for row in batch
        ]
        cursor.execute("DELETE FROM market_ticks WHERE time_ms < ?", (cutoff_ms,))
        cursor.executemany(sql, params)
        written = len(params)
        conn.commit()
    except Exception as exc:
        logger.error("Tick flush failed: %s", exc)
        conn.rollback()
        _buffer[:0] = batch
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


def query_ticks(
    symbol: str,
    from_ms: int,
    to_ms: int,
    limit: int | None = None,
    *,
    result_meta: dict | None = None,
) -> list[dict]:
    from app.config import ARCHIVE_TICK_QUERY_LIMIT

    lim = max(1, int(limit if limit is not None else ARCHIVE_TICK_QUERY_LIMIT))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Newest-N (+1 for truncation detect), return ASC.
        cursor.execute(
            """
            SELECT symbol, time_ms, price, volume, source, bid, ask, spread, tick_type
            FROM (
                SELECT symbol, time_ms, price, volume, source, bid, ask, spread, tick_type
                FROM market_ticks
                WHERE symbol = ? AND time_ms >= ? AND time_ms <= ?
                ORDER BY time_ms DESC
                LIMIT ?
            ) AS newest
            ORDER BY time_ms ASC
            """,
            (symbol, from_ms, to_ms, lim + 1),
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
                    "bid": row.get("bid"),
                    "ask": row.get("ask"),
                    "spread": row.get("spread"),
                    "tick_type": row.get("tick_type"),
                })
            else:
                out.append({
                    "symbol": row[0],
                    "time_ms": int(row[1]),
                    "price": float(row[2]),
                    "volume": float(row[3] or 0),
                    "source": row[4],
                    "bid": row[5] if len(row) > 5 else None,
                    "ask": row[6] if len(row) > 6 else None,
                    "spread": row[7] if len(row) > 7 else None,
                    "tick_type": row[8] if len(row) > 8 else None,
                })
        truncated = len(out) > lim
        if truncated:
            out = out[-lim:]
        if result_meta is not None:
            result_meta["truncated"] = truncated
            result_meta["limit"] = lim
            result_meta["count"] = len(out)
        return out
    finally:
        conn.close()


async def tick_flush_loop():
    from app.config import ARCHIVE_TICKS_ENABLED, ARCHIVE_TICK_FLUSH_INTERVAL, ARCHIVE_TICK_RETENTION_HOURS

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
