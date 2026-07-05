"""Broker backfill + gap repair for market_bars_1m with ingestion state tracking."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import (
    ARCHIVE_ENABLED,
    ARCHIVE_INGESTION_DAYS,
    ARCHIVE_INGESTION_GAP_SCAN_DAYS,
    ARCHIVE_INGESTION_MAX_GAPS_PER_RUN,
    ARCHIVE_INGESTION_SYMBOL_DELAY_SEC,
    ARCHIVE_RETENTION_1M_DAYS,
    SYMBOLS,
    TERMINAL_MODE,
)
from app.db.connection import db_session
from app.services.archive.backfill import (
    backfill_symbol_from_feed,
    backfill_symbol_from_parquet,
    run_archive_backfill,
)
from app.services.archive.broker_fetch import (
    chunk_date_ranges,
    fetch_broker_1m_bars,
    resolve_broker_source,
)
from app.services.archive.gap_scan import (
    filter_unknown_gaps,
    find_gap_ranges,
    get_symbol_bar_span,
    record_known_gap_range,
)
from app.services.archive.writer import _upsert_1m_rows

logger = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


def get_ingestion_state(symbol: str) -> dict[str, Any] | None:
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM archive_ingestion_state WHERE symbol = ?",
            (symbol,),
        )
        row = cursor.fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        return dict(row)
    cols = [
        "symbol", "last_bar_time", "oldest_bar_time", "bars_total",
        "last_backfill", "last_gap_scan", "last_error", "updated_at",
    ]
    return dict(zip(cols, row))


def _refresh_span_counts(symbol: str) -> dict[str, Any]:
    span = get_symbol_bar_span(symbol)
    now = _now()
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO archive_ingestion_state
                (symbol, last_bar_time, oldest_bar_time, bars_total, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (symbol) DO UPDATE SET
                last_bar_time = excluded.last_bar_time,
                oldest_bar_time = excluded.oldest_bar_time,
                bars_total = excluded.bars_total,
                updated_at = excluded.updated_at
            """,
            (
                symbol,
                span.get("newest"),
                span.get("oldest"),
                span.get("count") or 0,
                now,
            ),
        )
    return span


def _set_ingestion_error(symbol: str, error: str | None) -> None:
    now = _now()
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO archive_ingestion_state (symbol, last_error, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (symbol) DO UPDATE SET
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (symbol, error, now),
        )


def _mark_backfill(symbol: str, rows_written: int, *, gap_scan: bool = False) -> None:
    span = _refresh_span_counts(symbol)
    now = _now()
    with db_session() as conn:
        cursor = conn.cursor()
        if gap_scan:
            cursor.execute(
                """
                UPDATE archive_ingestion_state
                SET last_gap_scan = ?, last_error = NULL, updated_at = ?
                WHERE symbol = ?
                """,
                (now, now, symbol),
            )
        else:
            cursor.execute(
                """
                UPDATE archive_ingestion_state
                SET last_backfill = ?, last_error = NULL, updated_at = ?
                WHERE symbol = ?
                """,
                (now, now, symbol),
            )
    if rows_written:
        logger.info(
            "Archive ingest %s: %d rows (span %s → %s, count=%s)",
            symbol,
            rows_written,
            span.get("oldest"),
            span.get("newest"),
            span.get("count"),
        )


def ingest_broker_range(
    symbol: str,
    from_ts: int,
    to_ts: int,
    *,
    symbol_info: dict | None = None,
) -> int:
    """Fetch 1m bars from broker and upsert into market_bars_1m."""
    if not ARCHIVE_ENABLED or to_ts <= from_ts:
        return 0

    total = 0
    for chunk_from, chunk_to in chunk_date_ranges(from_ts, to_ts, chunk_days=30):
        rows = fetch_broker_1m_bars(
            symbol,
            chunk_from,
            chunk_to,
            symbol_info=symbol_info or SYMBOLS.get(symbol),
        )
        if rows:
            total += _upsert_1m_rows(rows)
    return total


def ingest_symbol_backfill(
    symbol: str,
    *,
    days: int | None = None,
    feed=None,
    include_parquet: bool = True,
) -> dict[str, Any]:
    """
    Ensure symbol has up to `days` of 1m history.

    Order: parquet seed → broker full/incremental → feed buffer fallback.
    """
    if not ARCHIVE_ENABLED:
        return {"symbol": symbol, "enabled": False, "rows_written": 0}

    target_days = min(
        int(days if days is not None else ARCHIVE_INGESTION_DAYS),
        int(ARCHIVE_RETENTION_1M_DAYS),
    )
    now = int(time.time())
    target_from = now - target_days * 86400
    source = TERMINAL_MODE or "SIMULATED"
    result: dict[str, Any] = {
        "symbol": symbol,
        "target_days": target_days,
        "parquet": 0,
        "broker": 0,
        "feed": 0,
        "gap_fill": 0,
        "rows_written": 0,
        "broker_source": resolve_broker_source(),
    }

    try:
        if include_parquet:
            parquet_rows = backfill_symbol_from_parquet(symbol)
            result["parquet"] = parquet_rows

        span = get_symbol_bar_span(symbol)
        oldest = span.get("oldest")
        newest = span.get("newest")
        full_fetch_to_now = False

        if oldest is None or oldest > target_from:
            broker_to = now
            broker_from = target_from if oldest is None else min(target_from, oldest - 60)
            broker_rows = ingest_broker_range(symbol, broker_from, broker_to)
            result["broker"] = broker_rows
            full_fetch_to_now = broker_to >= now - 60
            span = get_symbol_bar_span(symbol)
            oldest = span.get("oldest")
            newest = span.get("newest")

        if newest is not None and newest < now - 120 and not full_fetch_to_now:
            incr_rows = ingest_broker_range(symbol, newest + 60, now)
            result["broker"] += incr_rows
        elif newest is None and result["broker"] == 0:
            feed_rows = backfill_symbol_from_feed(feed, symbol, source) if feed else 0
            result["feed"] = feed_rows

        gap_rows = ingest_symbol_gaps(symbol, days=ARCHIVE_INGESTION_GAP_SCAN_DAYS)
        result["gap_fill"] = gap_rows

        result["rows_written"] = (
            result["parquet"] + result["broker"] + result["feed"] + result["gap_fill"]
        )
        if result["rows_written"] > 0:
            _mark_backfill(symbol, result["rows_written"])
        else:
            _refresh_span_counts(symbol)
        result["span"] = get_symbol_bar_span(symbol)
        return result
    except Exception as exc:
        logger.warning("Archive ingest failed for %s: %s", symbol, exc)
        _set_ingestion_error(symbol, str(exc))
        result["error"] = str(exc)
        return result


def ingest_symbol_gaps(symbol: str, *, days: int | None = None) -> int:
    """Scan recent archive for intraday holes and fetch missing bars."""
    if not ARCHIVE_ENABLED:
        return 0

    scan_days = int(days if days is not None else ARCHIVE_INGESTION_GAP_SCAN_DAYS)
    now = int(time.time())
    from_ts = now - scan_days * 86400
    gaps = find_gap_ranges(
        symbol,
        from_ts=from_ts,
        to_ts=now,
        max_gaps=ARCHIVE_INGESTION_MAX_GAPS_PER_RUN,
    )
    gaps = filter_unknown_gaps(symbol, gaps)
    if not gaps:
        return 0

    total = 0
    for gap_start, gap_end in gaps:
        written = ingest_broker_range(symbol, gap_start, gap_end)
        if written:
            total += written
        elif resolve_broker_source() != "none":
            record_known_gap_range(symbol, gap_start, gap_end, reason="no_bars")
    if total:
        _mark_backfill(symbol, total, gap_scan=True)
    else:
        _refresh_span_counts(symbol)
    return total


def run_archive_ingestion(
    symbols: list[str] | None = None,
    *,
    feed=None,
    days: int | None = None,
    include_seed_backfill: bool = True,
    max_symbols: int | None = None,
    symbol_delay_sec: float | None = None,
) -> dict[str, Any]:
    """Run broker ingestion for all symbols (sequential — respects API limits)."""
    if not ARCHIVE_ENABLED:
        return {"enabled": False, "symbols": 0, "rows_written": 0}

    all_syms = symbols or list(SYMBOLS.keys())
    syms = all_syms[:max_symbols] if max_symbols else all_syms
    delay = (
        float(symbol_delay_sec)
        if symbol_delay_sec is not None
        else ARCHIVE_INGESTION_SYMBOL_DELAY_SEC
    )
    seed_result: dict[str, Any] | None = None
    if include_seed_backfill:
        seed_result = run_archive_backfill(
            syms,
            feed=feed,
            source=TERMINAL_MODE or "SIMULATED",
            skip_existing=True,
            include_parquet=True,
            include_feed=True,
        )

    details: dict[str, dict] = {}
    total = int((seed_result or {}).get("rows_written") or 0)

    import time as _time
    from app.config import ARCHIVE_INGESTION_CONCURRENCY

    for idx, symbol in enumerate(syms):
        detail = ingest_symbol_backfill(
            symbol,
            days=days,
            feed=feed,
            include_parquet=not include_seed_backfill,
        )
        details[symbol] = detail
        total += int(detail.get("rows_written") or 0)
        if idx + 1 < len(syms) and delay > 0 and ARCHIVE_INGESTION_CONCURRENCY <= 1:
            _time.sleep(delay)

    return {
        "enabled": True,
        "broker_source": resolve_broker_source(),
        "symbols": len(syms),
        "symbols_total": len(all_syms),
        "symbols_deferred": max(0, len(all_syms) - len(syms)),
        "rows_written": total,
        "seed_backfill": seed_result,
        "details": details,
    }


def get_ingestion_summary() -> dict[str, Any]:
    """Summary for admin stats / diagnostics."""
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT symbol, last_bar_time, oldest_bar_time, bars_total,
                   last_backfill, last_gap_scan, last_error
            FROM archive_ingestion_state
            ORDER BY symbol
            """
        )
        rows = cursor.fetchall()

    states: list[dict[str, Any]] = []
    for row in rows or []:
        if isinstance(row, dict):
            states.append(dict(row))
        else:
            states.append({
                "symbol": row[0],
                "last_bar_time": row[1],
                "oldest_bar_time": row[2],
                "bars_total": row[3],
                "last_backfill": row[4],
                "last_gap_scan": row[5],
                "last_error": row[6],
            })

    now = int(time.time())
    target_from = now - ARCHIVE_INGESTION_DAYS * 86400
    shortfall: list[str] = []

    for symbol in SYMBOLS:
        span = get_symbol_bar_span(symbol)
        oldest = span.get("oldest")
        if oldest is None or oldest > target_from + 86400:
            shortfall.append(symbol)

    return {
        "broker_source": resolve_broker_source(),
        "broker_available": resolve_broker_source() != "none",
        "target_days": ARCHIVE_INGESTION_DAYS,
        "symbols_tracked": len(states),
        "symbols_shortfall": shortfall,
        "symbols_shortfall_count": len(shortfall),
        "states": states,
    }
