"""Durable bot signal idempotency and fill journal — survives process restarts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_connection, is_postgres

_INCOMPLETE_STATUSES = ("claimed", "submitted")
_TERMINAL_STATUSES = ("filled", "failed", "ambiguous")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def claim_signal(signal_id: str, bot_id: str, bar_time, signal_kind: str) -> bool:
    """Insert signal_id if absent. Returns True when this caller owns the signal."""
    if not signal_id or not bot_id:
        return False

    bar_time_val = int(bar_time) if bar_time is not None else None
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if is_postgres():
            cursor.execute(
                """
                INSERT INTO bot_signal_ledger (signal_id, bot_id, bar_time, signal_kind, status, updated_at)
                VALUES (?, ?, ?, ?, 'claimed', ?)
                ON CONFLICT (signal_id) DO NOTHING
                """,
                (signal_id, bot_id, bar_time_val, signal_kind, _now_iso()),
            )
        else:
            cursor.execute(
                """
                INSERT OR IGNORE INTO bot_signal_ledger
                    (signal_id, bot_id, bar_time, signal_kind, status, updated_at)
                VALUES (?, ?, ?, ?, 'claimed', ?)
                """,
                (signal_id, bot_id, bar_time_val, signal_kind, _now_iso()),
            )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def mark_signal_submitted(
    signal_id: str,
    *,
    order_id: str | None = None,
    broker: str | None = None,
    payload: dict | None = None,
) -> None:
    if not signal_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE bot_signal_ledger
            SET status = 'submitted',
                order_id = COALESCE(?, order_id),
                broker = COALESCE(?, broker),
                payload = COALESCE(?, payload),
                updated_at = ?
            WHERE signal_id = ? AND status IN ('claimed', 'submitted')
            """,
            (
                order_id,
                broker,
                json.dumps(payload) if payload is not None else None,
                _now_iso(),
                signal_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def mark_signal_filled(signal_id: str, *, order_id: str | None = None) -> None:
    if not signal_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE bot_signal_ledger
            SET status = 'filled',
                order_id = COALESCE(?, order_id),
                updated_at = ?
            WHERE signal_id = ?
            """,
            (order_id, _now_iso(), signal_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_signal_failed(signal_id: str, message: str = "") -> None:
    if not signal_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE bot_signal_ledger
            SET status = 'failed', message = ?, updated_at = ?
            WHERE signal_id = ? AND status IN ('claimed', 'submitted')
            """,
            (message[:500] if message else None, _now_iso(), signal_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_signal_ambiguous(signal_id: str, message: str = "", *, order_id: str | None = None) -> None:
    if not signal_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE bot_signal_ledger
            SET status = 'ambiguous',
                message = ?,
                order_id = COALESCE(?, order_id),
                updated_at = ?
            WHERE signal_id = ?
            """,
            (message[:500] if message else None, order_id, _now_iso(), signal_id),
        )
        conn.commit()
    finally:
        conn.close()


def release_signal(signal_id: str) -> None:
    """Allow retry after explicit pre-submit failure (removes ledger row)."""
    if not signal_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM bot_signal_ledger WHERE signal_id = ? AND status = 'claimed'",
            (signal_id,),
        )
        conn.commit()
    finally:
        conn.close()


def list_incomplete_signals(limit: int = 100) -> list[dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        placeholders = ",".join("?" for _ in _INCOMPLETE_STATUSES)
        cursor.execute(
            f"""
            SELECT signal_id, bot_id, bar_time, signal_kind, status, order_id, broker,
                   message, created_at, updated_at
            FROM bot_signal_ledger
            WHERE status IN ({placeholders})
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (*_INCOMPLETE_STATUSES, limit),
        )
        rows = cursor.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                out.append(dict(row))
            else:
                out.append({
                    "signal_id": row[0],
                    "bot_id": row[1],
                    "bar_time": row[2],
                    "signal_kind": row[3],
                    "status": row[4],
                    "order_id": row[5],
                    "broker": row[6],
                    "message": row[7],
                    "created_at": row[8],
                    "updated_at": row[9],
                })
        return out
    finally:
        conn.close()


def reconcile_orphaned_claims() -> int:
    """Mark never-submitted claims as failed after crash recovery."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE bot_signal_ledger
            SET status = 'failed',
                message = 'orphaned_claim: never submitted before shutdown',
                updated_at = ?
            WHERE status = 'claimed'
            """,
            (_now_iso(),),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def clear_signal_ledger() -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM bot_signal_ledger")
        conn.commit()
    finally:
        conn.close()
