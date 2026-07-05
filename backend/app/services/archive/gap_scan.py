"""Detect missing 1m bar ranges in market_bars_1m for targeted backfill."""

from __future__ import annotations

import time
from typing import Any

from app.config import ARCHIVE_ENABLED, DATA_QUALITY_GAP_BAR_SEC
from app.db.connection import db_session


def get_symbol_bar_span(symbol: str) -> dict[str, Any]:
    """Return oldest/newest/count for a symbol in market_bars_1m."""
    if not ARCHIVE_ENABLED:
        return {"symbol": symbol, "count": 0}
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT MIN(time), MAX(time), COUNT(*)
            FROM market_bars_1m
            WHERE symbol = ?
            """,
            (symbol,),
        )
        row = cursor.fetchone()
    if not row:
        return {"symbol": symbol, "count": 0}
    if isinstance(row, dict):
        oldest, newest, count = row.get("MIN(time)"), row.get("MAX(time)"), row.get("COUNT(*)")
    else:
        oldest, newest, count = row[0], row[1], row[2]
    return {
        "symbol": symbol,
        "oldest": int(oldest) if oldest is not None else None,
        "newest": int(newest) if newest is not None else None,
        "count": int(count or 0),
    }


def find_gap_ranges(
    symbol: str,
    *,
    from_ts: int | None = None,
    to_ts: int | None = None,
    bar_sec: int = 60,
    gap_threshold_sec: int | None = None,
    max_gaps: int = 20,
    max_gap_span_sec: int = 6 * 3600,
) -> list[tuple[int, int]]:
    """
    Return (gap_start, gap_end) unix ranges where consecutive bars are missing.

    Only returns intraday-sized holes (default < 6h) — multi-day holes are handled
    by full/incremental broker backfill, not per-gap repair.
    """
    if not ARCHIVE_ENABLED:
        return []

    now = int(time.time())
    to_ts = int(to_ts if to_ts is not None else now)
    from_ts = int(from_ts if from_ts is not None else now - 7 * 86400)
    threshold = int(gap_threshold_sec if gap_threshold_sec is not None else DATA_QUALITY_GAP_BAR_SEC)

    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT time FROM market_bars_1m
            WHERE symbol = ? AND time >= ? AND time <= ?
            ORDER BY time ASC
            """,
            (symbol, from_ts, to_ts),
        )
        times = [
            int(r[0] if not isinstance(r, dict) else r["time"])
            for r in cursor.fetchall()
        ]

    if len(times) < 2:
        return []

    gaps: list[tuple[int, int]] = []
    for i in range(1, len(times)):
        delta = times[i] - times[i - 1]
        if delta <= threshold:
            continue
        if delta > max_gap_span_sec:
            continue
        gap_start = times[i - 1] + bar_sec
        gap_end = times[i] - bar_sec
        if gap_start < gap_end:
            gaps.append((gap_start, gap_end))
        if len(gaps) >= max_gaps:
            break
    return gaps


def record_known_gap(symbol: str, bucket_time: int, reason: str = "unfillable") -> None:
    now = time.time()
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO archive_known_gaps (symbol, bucket_time, reason, recorded_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (symbol, bucket_time) DO UPDATE SET
                reason = excluded.reason,
                recorded_at = excluded.recorded_at
            """,
            (symbol, int(bucket_time), reason, now),
        )


def is_known_gap(symbol: str, bucket_time: int) -> bool:
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 1 FROM archive_known_gaps
            WHERE symbol = ? AND bucket_time = ?
            LIMIT 1
            """,
            (symbol, int(bucket_time)),
        )
        return cursor.fetchone() is not None


def record_known_gap_range(
    symbol: str,
    gap_start: int,
    gap_end: int,
    reason: str = "unfillable",
) -> None:
    """Remember a gap range that returned no broker bars (market closed / no data)."""
    record_known_gap(symbol, int(gap_start), f"{reason}:{int(gap_end)}")


def is_gap_range_known(symbol: str, gap_start: int, gap_end: int) -> bool:
    """True if this gap range was previously marked unfillable."""
    if not is_known_gap(symbol, gap_start):
        return False
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT reason FROM archive_known_gaps
            WHERE symbol = ? AND bucket_time = ?
            LIMIT 1
            """,
            (symbol, int(gap_start)),
        )
        row = cursor.fetchone()
    if not row:
        return False
    reason = row[0] if not isinstance(row, dict) else row.get("reason")
    if not reason:
        return True
    text = str(reason)
    if ":" in text:
        try:
            recorded_end = int(text.rsplit(":", 1)[-1])
            return recorded_end >= int(gap_end) - 60
        except ValueError:
            pass
    return True


def filter_unknown_gaps(
    symbol: str,
    gaps: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    return [
        (start, end)
        for start, end in gaps
        if not is_gap_range_known(symbol, start, end)
    ]

