"""Persistent risk monitor state (equity peak, kill-switch latch)."""

from __future__ import annotations

import time

from app.database import get_connection

KEY_EQUITY_PEAK = "equity_peak"
KEY_KILL_SWITCH_TRIPPED_AT = "kill_switch_tripped_at"


def _get_float(key: str) -> float | None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM risk_state WHERE key = ?", (key,))
        row = cursor.fetchone()
        if not row:
            return None
        val = row["value"] if isinstance(row, dict) else row[0]
        return float(val)
    finally:
        conn.close()


def _set_float(key: str, value: float) -> None:
    now = time.time()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO risk_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, float(value), now),
        )
        conn.commit()
    finally:
        conn.close()


def _delete_key(key: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM risk_state WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


def get_equity_peak() -> float | None:
    return _get_float(KEY_EQUITY_PEAK)


def set_equity_peak(equity: float) -> None:
    if equity > 0:
        _set_float(KEY_EQUITY_PEAK, equity)


def is_kill_switch_tripped() -> bool:
    return get_kill_switch_tripped_at() is not None


def get_kill_switch_tripped_at() -> float | None:
    ts = _get_float(KEY_KILL_SWITCH_TRIPPED_AT)
    if ts is None or ts <= 0:
        return None
    return ts


def trip_kill_switch(at: float | None = None) -> None:
    _set_float(KEY_KILL_SWITCH_TRIPPED_AT, at if at is not None else time.time())


def reset_kill_switch(*, current_equity: float | None = None) -> None:
    """Clear kill-switch latch; optionally re-base peak equity."""
    _delete_key(KEY_KILL_SWITCH_TRIPPED_AT)
    if current_equity is not None and current_equity > 0:
        set_equity_peak(current_equity)


def update_peak_if_higher(equity: float) -> float:
    """Bump stored peak when equity makes a new high; return effective peak."""
    peak = get_equity_peak()
    if peak is None or equity > peak:
        set_equity_peak(equity)
        return equity
    return peak
