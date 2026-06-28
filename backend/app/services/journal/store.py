"""Trade journal persistence."""

from __future__ import annotations

import json
import uuid

from app.database import get_connection
from app.services.journal.storage import get_screenshot_storage, resolve_screenshot_url

MAX_NOTE_LEN = 8000
MAX_TAGS = 20


def _parse_tags(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()][:MAX_TAGS]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return _parse_tags(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        return [t.strip() for t in raw.split(",") if t.strip()][:MAX_TAGS]
    return []


def _row_to_entry(row: dict) -> dict:
    return {
        "id": row["id"],
        "trade_ref": row.get("trade_ref"),
        "order_id": row.get("order_id"),
        "bot_id": row.get("bot_id"),
        "symbol": row.get("symbol"),
        "tags": _parse_tags(row.get("tags")),
        "note": row.get("note") or "",
        "lesson": row.get("lesson") or "",
        "screenshot_url": resolve_screenshot_url(row.get("screenshot_url")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_entries(
    *,
    query: str | None = None,
    tag: str | None = None,
    symbol: str | None = None,
    limit: int = 100,
) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    sql = "SELECT * FROM trade_journal WHERE 1=1"
    params: list = []
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol.strip().upper())
    if tag:
        sql += " AND tags LIKE ?"
        params.append(f'%"{tag.strip()}"%')
    if query:
        q = f"%{query.strip()}%"
        sql += " AND (note LIKE ? OR lesson LIKE ? OR symbol LIKE ? OR trade_ref LIKE ?)"
        params.extend([q, q, q, q])
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(min(max(limit, 1), 500))
    cursor.execute(sql, params)
    rows = [_row_to_entry(dict(r)) for r in cursor.fetchall()]
    conn.close()
    return rows


def upsert_entry(payload: dict) -> dict:
    entry_id = (payload.get("id") or "").strip() or str(uuid.uuid4())
    note = (payload.get("note") or "")[:MAX_NOTE_LEN]
    lesson = (payload.get("lesson") or "")[:MAX_NOTE_LEN]
    tags = _parse_tags(payload.get("tags"))
    tags_json = json.dumps(tags)
    symbol = (payload.get("symbol") or "").strip().upper() or None
    trade_ref = (payload.get("trade_ref") or "").strip() or None
    order_id = (payload.get("order_id") or "").strip() or None
    bot_id = (payload.get("bot_id") or "").strip() or None

    screenshot = payload.get("screenshot_url")
    if screenshot is not None:
        screenshot = get_screenshot_storage().save(screenshot)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM trade_journal WHERE id = ?", (entry_id,))
    exists = cursor.fetchone()

    if exists:
        if screenshot is not None:
            cursor.execute(
                """
                UPDATE trade_journal
                SET trade_ref=?, order_id=?, bot_id=?, symbol=?, tags=?, note=?, lesson=?,
                    screenshot_url=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (trade_ref, order_id, bot_id, symbol, tags_json, note, lesson, screenshot, entry_id),
            )
        else:
            cursor.execute(
                """
                UPDATE trade_journal
                SET trade_ref=?, order_id=?, bot_id=?, symbol=?, tags=?, note=?, lesson=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (trade_ref, order_id, bot_id, symbol, tags_json, note, lesson, entry_id),
            )
    else:
        cursor.execute(
            """
            INSERT INTO trade_journal
            (id, trade_ref, order_id, bot_id, symbol, tags, note, lesson, screenshot_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (entry_id, trade_ref, order_id, bot_id, symbol, tags_json, note, lesson, screenshot),
        )
    conn.commit()
    cursor.execute("SELECT * FROM trade_journal WHERE id = ?", (entry_id,))
    row = dict(cursor.fetchone())
    conn.close()
    return _row_to_entry(row)


def delete_entry(entry_id: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM trade_journal WHERE id = ?", (entry_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted
