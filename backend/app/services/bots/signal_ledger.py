"""Durable bot signal idempotency — survives process restarts."""

from __future__ import annotations

from app.db.connection import get_connection, is_postgres


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
                INSERT INTO bot_signal_ledger (signal_id, bot_id, bar_time, signal_kind, status)
                VALUES (?, ?, ?, ?, 'claimed')
                ON CONFLICT (signal_id) DO NOTHING
                """,
                (signal_id, bot_id, bar_time_val, signal_kind),
            )
        else:
            cursor.execute(
                """
                INSERT OR IGNORE INTO bot_signal_ledger
                    (signal_id, bot_id, bar_time, signal_kind, status)
                VALUES (?, ?, ?, ?, 'claimed')
                """,
                (signal_id, bot_id, bar_time_val, signal_kind),
            )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def mark_signal_filled(signal_id: str) -> None:
    if not signal_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE bot_signal_ledger SET status = 'filled' WHERE signal_id = ?",
            (signal_id,),
        )
        conn.commit()
    finally:
        conn.close()


def release_signal(signal_id: str) -> None:
    """Allow retry after explicit order failure (removes ledger row)."""
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


def clear_signal_ledger() -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM bot_signal_ledger")
        conn.commit()
    finally:
        conn.close()
