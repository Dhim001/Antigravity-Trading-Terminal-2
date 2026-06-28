"""Persistent runtime flags — unclean shutdown detection, safe mode, bot checkpoints."""

from __future__ import annotations

import json
import time
from typing import Any

from app.db.connection import get_connection

KEY_SHUTDOWN_CLEAN = "shutdown_clean"
KEY_UNCLEAN_BOOT = "unclean_boot_detected"
KEY_SAFE_MODE_ACTIVE = "safe_mode_active"
KEY_SAFE_MODE_REASON = "safe_mode_reason"
KEY_BOT_RUNTIME_CHECKPOINT = "bot_runtime_checkpoint"


def _now() -> float:
    return time.time()


def _get_text(key: str) -> str | None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM system_runtime WHERE key = ?", (key,))
        row = cursor.fetchone()
        if not row:
            return None
        return row["value"] if isinstance(row, dict) else row[0]
    finally:
        conn.close()


def _set_text(key: str, value: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO system_runtime (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def _delete_key(key: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM system_runtime WHERE key = ?", (key,))
        conn.commit()
    finally:
        conn.close()


def mark_process_starting() -> bool:
    """Mark boot in progress. Returns True when the previous run did not shut down cleanly."""
    was_clean = _get_text(KEY_SHUTDOWN_CLEAN)
    unclean = was_clean is not None and was_clean not in ("1", "true")
    _set_text(KEY_UNCLEAN_BOOT, "1" if unclean else "0")
    _set_text(KEY_SHUTDOWN_CLEAN, "0")
    return unclean


def mark_shutdown_clean() -> None:
    _set_text(KEY_SHUTDOWN_CLEAN, "1")
    _set_text(KEY_UNCLEAN_BOOT, "0")


def was_unclean_shutdown() -> bool:
    flag = _get_text(KEY_UNCLEAN_BOOT)
    if flag is not None:
        return flag == "1"
    return _get_text(KEY_SHUTDOWN_CLEAN) not in ("1", "true")


def enter_safe_mode(reason: str, *, details: dict | None = None) -> None:
    payload = {"reason": reason, **(details or {})}
    _set_text(KEY_SAFE_MODE_ACTIVE, "1")
    _set_text(KEY_SAFE_MODE_REASON, json.dumps(payload))


def clear_safe_mode() -> None:
    _delete_key(KEY_SAFE_MODE_ACTIVE)
    _delete_key(KEY_SAFE_MODE_REASON)


def is_safe_mode_active() -> bool:
    return _get_text(KEY_SAFE_MODE_ACTIVE) == "1"


def get_safe_mode_info() -> dict[str, Any]:
    if not is_safe_mode_active():
        return {"active": False}
    raw = _get_text(KEY_SAFE_MODE_REASON) or "{}"
    try:
        reason_payload = json.loads(raw)
    except json.JSONDecodeError:
        reason_payload = {"reason": raw}
    return {"active": True, **reason_payload}


def save_bot_runtime_checkpoint(checkpoint: dict[str, Any]) -> None:
    _set_text(KEY_BOT_RUNTIME_CHECKPOINT, json.dumps(checkpoint))


def load_bot_runtime_checkpoint() -> dict[str, Any]:
    raw = _get_text(KEY_BOT_RUNTIME_CHECKPOINT)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def clear_bot_runtime_checkpoint() -> None:
    _delete_key(KEY_BOT_RUNTIME_CHECKPOINT)


def runtime_status_dict() -> dict[str, Any]:
    from app.services.bots import signal_ledger

    incomplete = signal_ledger.list_incomplete_signals()
    return {
        "shutdown_clean": not was_unclean_shutdown(),
        "safe_mode": get_safe_mode_info(),
        "incomplete_journal_count": len(incomplete),
        "incomplete_journal": incomplete[:20],
    }
