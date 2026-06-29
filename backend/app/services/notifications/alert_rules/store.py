"""Alert rule CRUD + trigger log."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.db.connection import get_connection
from app.services.market.timeframes import normalize_timeframe
from app.services.notifications.alert_rules import types as atypes


def _now() -> float:
    return time.time()


def _row_to_dict(row) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    cols = (
        "id", "name", "enabled", "symbol", "timeframe", "condition_type",
        "threshold", "signal", "cooldown_sec", "notify_channels",
        "last_triggered_at", "created_at", "updated_at",
    )
    return {cols[i]: row[i] for i in range(min(len(row), len(cols)))}


def _parse_channels(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            return [str(x) for x in data if x] if isinstance(data, list) else []
        except Exception:
            return []
    return []


def rule_to_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": bool(row.get("enabled")),
        "symbol": row["symbol"],
        "timeframe": row.get("timeframe") or "1m",
        "condition_type": row["condition_type"],
        "threshold": row.get("threshold"),
        "signal": row.get("signal"),
        "cooldown_sec": int(row.get("cooldown_sec") or 300),
        "notify_channels": _parse_channels(row.get("notify_channels")),
        "last_triggered_at": row.get("last_triggered_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_rules(*, symbol: str | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if symbol:
            cursor.execute(
                "SELECT * FROM alert_rules WHERE symbol = ? ORDER BY name",
                (symbol.upper(),),
            )
        else:
            cursor.execute("SELECT * FROM alert_rules ORDER BY symbol, name")
        return [rule_to_public(_row_to_dict(r)) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_rule(rule_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM alert_rules WHERE id = ?", (rule_id,))
        row = cursor.fetchone()
        return rule_to_public(_row_to_dict(row)) if row else None
    finally:
        conn.close()


def list_enabled_for_symbol(symbol: str) -> list[dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT * FROM alert_rules
            WHERE enabled = 1 AND symbol = ?
            ORDER BY timeframe, name
            """,
            (symbol.upper(),),
        )
        return [rule_to_public(_row_to_dict(r)) for r in cursor.fetchall()]
    finally:
        conn.close()


def upsert_rule(
    *,
    rule_id: str | None,
    name: str,
    enabled: bool,
    symbol: str,
    timeframe: str,
    condition_type: str,
    threshold: float | None,
    signal: str | None,
    cooldown_sec: int,
    notify_channels: list[str],
) -> dict[str, Any]:
    if condition_type not in atypes.ALL_CONDITION_TYPES:
        raise ValueError(f"Unknown condition type: {condition_type}")
    if condition_type in atypes.NEEDS_THRESHOLD and threshold is None:
        raise ValueError("threshold is required for this condition type")
    if condition_type in atypes.NEEDS_SIGNAL and not signal:
        raise ValueError("signal is required for signal_is rules")

    sym = symbol.upper().strip()
    if not sym:
        raise ValueError("symbol is required")
    tf = normalize_timeframe(timeframe or "1m")
    rid = rule_id or str(uuid.uuid4())
    now = _now()
    channels_json = json.dumps(notify_channels or [])

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM alert_rules WHERE id = ?", (rid,))
        exists = cursor.fetchone() is not None
        if exists:
            cursor.execute(
                """
                UPDATE alert_rules SET
                    name = ?, enabled = ?, symbol = ?, timeframe = ?,
                    condition_type = ?, threshold = ?, signal = ?,
                    cooldown_sec = ?, notify_channels = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    name, int(enabled), sym, tf, condition_type,
                    threshold, signal, int(cooldown_sec), channels_json, now, rid,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO alert_rules (
                    id, name, enabled, symbol, timeframe, condition_type,
                    threshold, signal, cooldown_sec, notify_channels,
                    last_triggered_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    rid, name, int(enabled), sym, tf, condition_type,
                    threshold, signal, int(cooldown_sec), channels_json, now, now,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return get_rule(rid) or {"id": rid}


def delete_rule(rule_id: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def is_in_cooldown(rule: dict[str, Any], *, now: float | None = None) -> bool:
    last = rule.get("last_triggered_at")
    if last is None:
        return False
    cooldown = int(rule.get("cooldown_sec") or 300)
    return (now or _now()) - float(last) < cooldown


def mark_triggered(rule_id: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    now = _now()
    try:
        cursor.execute(
            "UPDATE alert_rules SET last_triggered_at = ?, updated_at = ? WHERE id = ?",
            (now, now, rule_id),
        )
        conn.commit()
    finally:
        conn.close()


def log_trigger(
    *,
    rule_id: str,
    symbol: str,
    timeframe: str,
    message: str,
    payload: dict[str, Any],
) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO alert_rule_log (id, rule_id, symbol, timeframe, message, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                rule_id,
                symbol.upper(),
                timeframe,
                message,
                json.dumps(payload),
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_trigger_history(*, rule_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    limit = max(1, min(int(limit), 200))
    try:
        if rule_id:
            cursor.execute(
                """
                SELECT * FROM alert_rule_log
                WHERE rule_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (rule_id, limit),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM alert_rule_log
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        out = []
        for row in cursor.fetchall():
            data = _row_to_dict(row)
            payload = data.get("payload_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            out.append({
                "id": data["id"],
                "rule_id": data["rule_id"],
                "symbol": data["symbol"],
                "timeframe": data["timeframe"],
                "message": data["message"],
                "payload": payload or {},
                "created_at": data.get("created_at"),
            })
        return out
    finally:
        conn.close()
