"""Range reads for archived market history."""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator

from app.config import (
    FOOTPRINT_CHUNK_MS,
    FOOTPRINT_MAX_CELLS,
    FOOTPRINT_MAX_RANGE_MS,
)
from app.db.connection import get_connection

logger = logging.getLogger(__name__)


def archive_query_limit(purpose: str = "default", limit: int | None = None) -> int:
    """Resolve bar LIMIT: explicit override > UI purpose > backtest/default."""
    from app.config import ARCHIVE_QUERY_LIMIT, ARCHIVE_QUERY_LIMIT_UI

    if limit is not None:
        return max(1, int(limit))
    if (purpose or "default").lower() == "ui":
        return max(1, int(ARCHIVE_QUERY_LIMIT_UI))
    return max(1, int(ARCHIVE_QUERY_LIMIT))


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


def iter_table_bars(
    table: str,
    symbol: str,
    from_ts: int,
    to_ts: int,
    *,
    limit: int | None = None,
    batch_size: int | None = None,
    purpose: str = "default",
    fetch_limit: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield archive OHLCV bars via ``fetchmany`` (newest-N in the window, ASC order)."""
    from app.config import ARCHIVE_QUERY_BATCH_SIZE

    lim = archive_query_limit(purpose, limit)
    # Optional fetch_limit > lim lets callers detect truncation (fetch lim+1).
    row_cap = max(1, int(fetch_limit if fetch_limit is not None else lim))
    batch = max(1, int(batch_size if batch_size is not None else ARCHIVE_QUERY_BATCH_SIZE))
    if row_cap <= 0:
        return

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Newest-N then ASC so caps keep the recent tail (live merge / charts).
        cursor.execute(
            f"""
            SELECT symbol, time, open, high, low, close, volume FROM (
                SELECT symbol, time, open, high, low, close, volume
                FROM {table}
                WHERE symbol = ? AND time >= ? AND time <= ?
                ORDER BY time DESC
                LIMIT ?
            ) AS newest
            ORDER BY time ASC
            """,
            (symbol, from_ts, to_ts, row_cap),
        )
        fetched = 0
        while fetched < row_cap:
            rows = cursor.fetchmany(min(batch, row_cap - fetched))
            if not rows:
                break
            for row in rows:
                yield _row_to_bar(row)
                fetched += 1
                if fetched >= row_cap:
                    break
    finally:
        conn.close()


def _query_table(
    table: str,
    symbol: str,
    from_ts: int,
    to_ts: int,
    limit: int | None = None,
    *,
    purpose: str = "default",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """List API for chart/history — newest-N capped; returns (bars, meta)."""
    lim = archive_query_limit(purpose, limit)
    # Fetch one extra to distinguish "exactly lim bars" from "hit the cap".
    raw = list(
        iter_table_bars(
            table,
            symbol,
            from_ts,
            to_ts,
            limit=lim,
            purpose=purpose,
            fetch_limit=lim + 1,
        )
    )
    truncated = len(raw) > lim
    bars = raw[-lim:] if truncated else raw
    meta = {
        "truncated": truncated,
        "limit": lim,
        "count": len(bars),
        "purpose": purpose,
    }
    return bars, meta


def query_1m(
    symbol: str,
    from_ts: int,
    to_ts: int,
    *,
    limit: int | None = None,
    purpose: str = "default",
) -> list[dict[str, Any]]:
    bars, _ = _query_table(
        "market_bars_1m", symbol, from_ts, to_ts, limit=limit, purpose=purpose
    )
    return bars


def query_1h(
    symbol: str,
    from_ts: int,
    to_ts: int,
    *,
    limit: int | None = None,
    purpose: str = "default",
) -> list[dict[str, Any]]:
    bars, _ = _query_table(
        "market_bars_1h", symbol, from_ts, to_ts, limit=limit, purpose=purpose
    )
    return bars


def query_market_history(
    symbol: str,
    from_ts: int | None = None,
    to_ts: int | None = None,
    interval: str = "auto",
    *,
    limit: int | None = None,
    purpose: str = "default",
    result_meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    bars, meta = query_market_history_detailed(
        symbol,
        from_ts=from_ts,
        to_ts=to_ts,
        interval=interval,
        limit=limit,
        purpose=purpose,
    )
    if result_meta is not None:
        result_meta.update(meta)
    return bars


def query_market_history_detailed(
    symbol: str,
    from_ts: int | None = None,
    to_ts: int | None = None,
    interval: str = "auto",
    *,
    limit: int | None = None,
    purpose: str = "default",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from app.config import ARCHIVE_RETENTION_1M_DAYS

    now = int(time.time())
    to_ts = int(to_ts if to_ts is not None else now)
    from_ts = int(from_ts if from_ts is not None else now - 86400 * 7)

    if from_ts > to_ts:
        from_ts, to_ts = to_ts, from_ts

    interval = (interval or "auto").lower()
    cutoff_1m = now - int(ARCHIVE_RETENTION_1M_DAYS * 86400)
    lim = archive_query_limit(purpose, limit)
    truncated = False

    if interval == "1m":
        bars, meta = _query_table(
            "market_bars_1m", symbol, from_ts, to_ts, limit=lim, purpose=purpose
        )
        return bars, meta
    if interval == "1h":
        bars, meta = _query_table(
            "market_bars_1h", symbol, from_ts, to_ts, limit=lim, purpose=purpose
        )
        return bars, meta

    bars: list[dict[str, Any]] = []
    if to_ts > cutoff_1m:
        part, m1 = _query_table(
            "market_bars_1m",
            symbol,
            max(from_ts, cutoff_1m),
            to_ts,
            limit=lim,
            purpose=purpose,
        )
        bars.extend(part)
        truncated = truncated or bool(m1.get("truncated"))
    if from_ts < cutoff_1m:
        part, m2 = _query_table(
            "market_bars_1h",
            symbol,
            from_ts,
            min(to_ts, cutoff_1m - 60),
            limit=lim,
            purpose=purpose,
        )
        bars.extend(part)
        truncated = truncated or bool(m2.get("truncated"))

    deduped: dict[int, dict[str, Any]] = {}
    for bar in bars:
        deduped[bar["time"]] = bar
    out = [deduped[t] for t in sorted(deduped)]
    # Mixed auto path can exceed one table LIMIT; keep newest.
    if len(out) > lim:
        out = out[-lim:]
        truncated = True

    meta = {
        "truncated": truncated,
        "limit": lim,
        "count": len(out),
        "purpose": purpose,
        "interval": interval,
    }
    return out, meta


def _iter_ms_chunks(from_ts: int, to_ts: int, chunk_ms: int) -> Iterator[tuple[int, int]]:
    """Inclusive ms windows of at most ``chunk_ms`` width."""
    step = max(1, int(chunk_ms))
    cur = int(from_ts)
    end = int(to_ts)
    while cur <= end:
        chunk_end = min(cur + step - 1, end)
        yield cur, chunk_end
        cur = chunk_end + 1


def _footprint_row(row) -> tuple[int, float, float]:
    if isinstance(row, dict):
        return int(row["bucket_time"]), float(row["bucket_price"]), float(row["total_volume"] or 0)
    return int(row[0]), float(row[1]), float(row[2] or 0)


def query_footprint_detailed(
    symbol: str,
    from_ts: int,
    to_ts: int,
    price_step: float,
    time_bucket_ms: int = 60000,
    *,
    max_range_ms: int | None = None,
    chunk_ms: int | None = None,
    max_cells: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Aggregate market ticks into a volume footprint heatmap.

    Runs GROUP BY per time chunk so SQLite never materializes the full-range
    aggregate at once. Caps range and cell count to avoid huge JSON payloads.
    Returns (cells, meta) where cells are ``{time, price, volume}``.
    """
    meta: dict[str, Any] = {
        "clamped": False,
        "truncated": False,
        "chunks": 0,
        "cell_count": 0,
    }
    if price_step <= 0 or time_bucket_ms <= 0:
        return [], meta

    from_ts = int(from_ts)
    to_ts = int(to_ts)
    if from_ts > to_ts:
        from_ts, to_ts = to_ts, from_ts

    range_cap = int(max_range_ms if max_range_ms is not None else FOOTPRINT_MAX_RANGE_MS)
    chunk = int(chunk_ms if chunk_ms is not None else FOOTPRINT_CHUNK_MS)
    cell_cap = int(max_cells if max_cells is not None else FOOTPRINT_MAX_CELLS)
    chunk = max(time_bucket_ms, chunk)
    range_cap = max(time_bucket_ms, range_cap)
    cell_cap = max(1, cell_cap)

    requested_from, requested_to = from_ts, to_ts
    span = to_ts - from_ts
    if span > range_cap:
        from_ts = to_ts - range_cap
        meta["clamped"] = True
        meta["range_note"] = (
            f"Range clamped to newest {range_cap}ms "
            f"(requested {requested_from}–{requested_to})"
        )

    meta["from_ts"] = from_ts
    meta["to_ts"] = to_ts

    accum: dict[tuple[int, float], float] = {}
    conn = get_connection()
    cursor = conn.cursor()
    from app.config import ARCHIVE_QUERY_BATCH_SIZE

    try:
        for chunk_from, chunk_to in _iter_ms_chunks(from_ts, to_ts, chunk):
            meta["chunks"] += 1
            cursor.execute(
                """
                SELECT
                    (time_ms / ?) * ? AS bucket_time,
                    CAST(price / ? AS INTEGER) * ? AS bucket_price,
                    SUM(volume) AS total_volume
                FROM market_ticks
                WHERE symbol = ? AND time_ms >= ? AND time_ms <= ?
                GROUP BY bucket_time, bucket_price
                ORDER BY bucket_time, bucket_price
                """,
                (
                    time_bucket_ms,
                    time_bucket_ms,
                    price_step,
                    price_step,
                    symbol,
                    chunk_from,
                    chunk_to,
                ),
            )
            while True:
                rows = cursor.fetchmany(ARCHIVE_QUERY_BATCH_SIZE)
                if not rows:
                    break
                for row in rows:
                    bt, bp, vol = _footprint_row(row)
                    key = (bt, bp)
                    if key in accum:
                        accum[key] += vol
                    elif len(accum) < cell_cap:
                        accum[key] = vol
                    else:
                        meta["truncated"] = True
                        break
                if meta["truncated"]:
                    break
            if meta["truncated"]:
                break
    except Exception as exc:
        logger.error("Footprint query failed: %s", exc)
        return [], {**meta, "error": str(exc)}
    finally:
        conn.close()

    cells = [
        {"time": t, "price": p, "volume": v}
        for (t, p), v in sorted(accum.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    ]
    meta["cell_count"] = len(cells)
    if meta["truncated"]:
        meta["range_note"] = (
            (meta.get("range_note") + " · " if meta.get("range_note") else "")
            + f"Cell cap reached ({cell_cap}); later buckets omitted"
        )
    return cells, meta


def query_footprint(
    symbol: str,
    from_ts: int,
    to_ts: int,
    price_step: float,
    time_bucket_ms: int = 60000,
) -> list[dict[str, Any]]:
    """Aggregate market ticks into a volume footprint heatmap (cells only)."""
    cells, _meta = query_footprint_detailed(
        symbol, from_ts, to_ts, price_step, time_bucket_ms
    )
    return cells


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
