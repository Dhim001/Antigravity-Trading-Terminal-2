"""Reject telemetry for silent NONEs.

Phase 4.11 of the Signal Enhancement Plan.

When a strategy returns ``NONE`` (no actionable signal), or a gate blocks a
would-be signal, the bot historically just ``continue``d the loop silently.
That made it impossible to answer "why are we not trading?" — every gate
reject, every low-confidence skip, every HTF misalignment vanished without
a trace.

This module records every such silent rejection into a durable table
(``bot_signal_reject_log``) with a coarse reason bucket so operators can
aggregate, chart, and tune gates:

    reason_bucket    | meaning
    -----------------+------------------------------------------
    none             | strategy itself returned NONE (no edge)
    low_confidence   | confidence below strategy threshold
    htf_gate         | higher-timeframe bias disagreed
    meta_label       | meta-label filter rejected the signal
    conformal        | conformal gate rejected (ambiguous set)
    regime_gate      | HMM regime gate attenuated to zero
    stacking         | stacking meta-learner margin too small
    llm_firewall     | LLM debate firewall vetoed
    filter           | chart-agent structural filter
    duplicate        | same bar already signaled
    other            | uncategorised

The recorder is a no-op when the table is unavailable (e.g. in tests that
use an in-memory or absent DB), so it never breaks the bot loop.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.db.connection import db_session, is_postgres

logger = logging.getLogger(__name__)

# Canonical reason buckets. Anything else is mapped to "other".
KNOWN_BUCKETS = {
    "none", "low_confidence", "htf_gate", "meta_label", "conformal",
    "regime_gate", "stacking", "llm_firewall", "filter", "duplicate",
    "pretrade_streak",
    "other",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_bucket(bucket: str | None) -> str:
    if not bucket:
        return "other"
    b = bucket.strip().lower()
    return b if b in KNOWN_BUCKETS else "other"


def record_reject(
    *,
    bot_id: str,
    reason_bucket: str,
    symbol: str | None = None,
    strategy: str | None = None,
    signal_kind: str | None = None,
    reason_detail: str | None = None,
    confidence: float | None = None,
    bar_time: int | None = None,
) -> bool:
    """Persist one silent-NONE / gate-reject event.

    Returns True on successful insert, False on any failure (never raises).
    Safe to call from inside the bot loop.
    """
    if not bot_id:
        return False
    bucket = _normalize_bucket(reason_bucket)
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bot_signal_reject_log
                    (bot_id, symbol, strategy, signal_kind, reason_bucket,
                     reason_detail, confidence, bar_time, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bot_id,
                    symbol,
                    strategy,
                    signal_kind,
                    bucket,
                    (reason_detail or "")[:500] or None,
                    float(confidence) if confidence is not None else None,
                    int(bar_time) if bar_time is not None else None,
                    _now_iso(),
                ),
            )
            return True
    except Exception:
        logger.debug("record_reject failed (non-fatal)", exc_info=True)
        return False


# ── Aggregation / query helpers ────────────────────────────────────────────


def reject_counts(
    *,
    bot_id: str | None = None,
    since_hours: float | None = None,
) -> dict[str, int]:
    """Return ``{reason_bucket: count}`` for the given scope."""
    where = []
    params: list[Any] = []
    if bot_id:
        where.append("bot_id = ?")
        params.append(bot_id)
    if since_hours is not None:
        if is_postgres():
            where.append("created_at >= NOW() - (? || ' hours')::INTERVAL")
            params.append(str(float(since_hours)))
        else:
            where.append("created_at >= datetime('now', ?)")
            params.append(f"-{float(since_hours)} hours")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT reason_bucket, COUNT(*) FROM bot_signal_reject_log {clause} GROUP BY reason_bucket",
                params,
            )
            return {row[0]: int(row[1]) for row in cursor.fetchall()}
    except Exception:
        logger.debug("reject_counts failed (non-fatal)", exc_info=True)
        return {}


def reject_breakdown_by_bot(
    *, since_hours: float | None = None
) -> dict[str, dict[str, int]]:
    """Return ``{bot_id: {reason_bucket: count}}``."""
    where = []
    params: list[Any] = []
    if since_hours is not None:
        if is_postgres():
            where.append("created_at >= NOW() - (? || ' hours')::INTERVAL")
            params.append(str(float(since_hours)))
        else:
            where.append("created_at >= datetime('now', ?)")
            params.append(f"-{float(since_hours)} hours")
    clause = ("WHERE " + where[0]) if where else ""
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT bot_id, reason_bucket, COUNT(*)
                FROM bot_signal_reject_log {clause}
                GROUP BY bot_id, reason_bucket
                """,
                params,
            )
            out: dict[str, dict[str, int]] = defaultdict(dict)
            for bot_id, bucket, cnt in cursor.fetchall():
                out[bot_id][bucket] = int(cnt)
            return dict(out)
    except Exception:
        logger.debug("reject_breakdown_by_bot failed (non-fatal)", exc_info=True)
        return {}


def clear_reject_log() -> int:
    """Wipe the reject log. Returns rows deleted (or -1 on failure)."""
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM bot_signal_reject_log")
            return cursor.rowcount or 0
    except Exception:
        return -1


# ── Retention (#29) — rollup daily counts, then delete raw rows ───────────


def _rollup_and_delete(conn, where_sql: str, params: Iterable[Any]) -> int:
    """Aggregate matching rows into ``bot_signal_reject_rollup`` then delete them.

    Keeps long-term reject statistics alive after raw-row retention pruning.
    """
    cursor = conn.cursor()
    cursor.execute(
        f"""
        INSERT INTO bot_signal_reject_rollup (day, bot_id, reason_bucket, count)
        SELECT substr(created_at, 1, 10), bot_id, reason_bucket, COUNT(*)
        FROM bot_signal_reject_log
        WHERE {where_sql}
        GROUP BY 1, 2, 3
        ON CONFLICT (day, bot_id, reason_bucket)
        DO UPDATE SET count = count + excluded.count
        """,
        params,
    )
    cursor.execute(f"DELETE FROM bot_signal_reject_log WHERE {where_sql}", params)
    return cursor.rowcount or 0


def prune_reject_log(retention_days: int, *, max_rows: int | None = None) -> int:
    """Retention pass for ``bot_signal_reject_log`` (MEMORY_CENTRIC_REVIEW #29).

    Rolls rows up into ``bot_signal_reject_rollup`` (day × bot × bucket counts)
    before deleting, so long-term stats survive. Deletes rows older than
    ``retention_days`` and, when ``max_rows`` is set, the oldest rows beyond
    that cap. Returns total rows deleted (never raises).
    """
    deleted = 0
    try:
        with db_session() as conn:
            if retention_days and retention_days > 0:
                cutoff = (
                    datetime.now(timezone.utc) - timedelta(days=int(retention_days))
                ).isoformat().replace("+00:00", "Z")
                deleted += _rollup_and_delete(conn, "created_at < ?", (cutoff,))
            if max_rows and max_rows > 0:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM bot_signal_reject_log ORDER BY id DESC LIMIT 1 OFFSET ?",
                    (int(max_rows),),
                )
                row = cursor.fetchone()
                threshold = row[0] if row else None
                if threshold is not None:
                    deleted += _rollup_and_delete(conn, "id <= ?", (threshold,))
    except Exception:
        logger.debug("prune_reject_log failed (non-fatal)", exc_info=True)
    return deleted


def reject_rollup(*, since_day: str | None = None) -> dict[str, dict[str, int]]:
    """Long-term stats from the rollup table: ``{bot_id: {bucket: count}}``."""
    where = "WHERE day >= ?" if since_day else ""
    params: tuple[Any, ...] = (since_day,) if since_day else ()
    try:
        with db_session() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT bot_id, reason_bucket, SUM(count)
                FROM bot_signal_reject_rollup {where}
                GROUP BY bot_id, reason_bucket
                """,
                params,
            )
            out: dict[str, dict[str, int]] = defaultdict(dict)
            for bot_id, bucket, cnt in cursor.fetchall():
                out[bot_id][bucket] = int(cnt)
            return dict(out)
    except Exception:
        logger.debug("reject_rollup failed (non-fatal)", exc_info=True)
        return {}


# ── Convenience: classify a strategy's reject_reason into a bucket ────────


def classify_reject(signal_data: dict, *, gate: str | None = None) -> str:
    """Best-effort bucket for a rejected signal dict.

    ``gate`` is an explicit override (e.g. "htf_gate", "conformal") when the
    caller already knows which gate fired. Otherwise we infer from
    ``signal_data`` keys.
    """
    if gate:
        return _normalize_bucket(gate)
    if not signal_data:
        return "none"
    if signal_data.get("reject_reason"):
        r = str(signal_data["reject_reason"]).lower()
        if "confiden" in r or "low" in r:
            return "low_confidence"
        if "filter" in r:
            return "filter"
        if "meta" in r:
            return "meta_label"
        if "conformal" in r:
            return "conformal"
        if "regime" in r:
            return "regime_gate"
        if "stack" in r:
            return "stacking"
        if "llm" in r or "firewall" in r or "debate" in r:
            return "llm_firewall"
        if "htf" in r or "bias" in r:
            return "htf_gate"
        if "pretrade" in r or "streak" in r:
            return "pretrade_streak"
        return "other"
    # No reject_reason → strategy simply returned NONE
    return "none"
