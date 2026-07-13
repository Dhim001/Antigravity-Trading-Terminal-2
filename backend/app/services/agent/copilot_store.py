"""Persistence for TRADE_COPILOT chat sessions."""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.db.connection import db_session


def _row_to_message(row: dict) -> dict[str, Any]:
    payload = {}
    raw = row.get("payload_json")
    if isinstance(raw, dict):
        payload = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row.get("content") or "",
        "intent": row.get("intent"),
        "payload": payload if isinstance(payload, dict) else {},
        "created_at": row.get("created_at"),
    }


def ensure_session_id(session_id: str | None) -> str:
    if session_id and str(session_id).strip():
        return str(session_id).strip()
    return str(uuid.uuid4())


def append_message(
    session_id: str,
    role: str,
    content: str,
    *,
    intent: str | None = None,
    payload: dict | None = None,
) -> dict[str, Any]:
    msg_id = str(uuid.uuid4())
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO copilot_messages (id, session_id, role, content, intent, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                msg_id,
                session_id,
                role,
                content or "",
                intent,
                json.dumps(payload or {}),
            ),
        )
        cursor.execute("SELECT * FROM copilot_messages WHERE id = ?", (msg_id,))
        row = dict(cursor.fetchone())
    return _row_to_message(row)


def list_messages(session_id: str, *, limit: int = 40) -> list[dict[str, Any]]:
    if not session_id:
        return []
    lim = max(1, min(int(limit), 200))
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM copilot_messages
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, lim),
        )
        rows = [dict(r) for r in cursor.fetchall()]
    rows.reverse()
    return [_row_to_message(r) for r in rows]


def clear_session(session_id: str) -> int:
    if not session_id:
        return 0
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM copilot_messages WHERE session_id = ?", (session_id,))
        return int(cursor.rowcount or 0)
