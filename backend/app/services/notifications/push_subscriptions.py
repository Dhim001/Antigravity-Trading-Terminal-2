"""Browser push subscription persistence."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.db.connection import get_connection
from app.services.notifications.crypto import decrypt_config, encrypt_config, mask_secret


def _now() -> float:
    return time.time()


def _row_to_dict(row) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    cols = (
        "id", "channel_id", "endpoint", "keys_encrypted", "user_agent",
        "created_at", "updated_at",
    )
    return {cols[i]: row[i] for i in range(min(len(row), len(cols)))}


def _keys_from_encrypted(blob: str) -> dict[str, str]:
    try:
        data = decrypt_config(blob or "")
        return {
            "p256dh": str(data.get("p256dh") or ""),
            "auth": str(data.get("auth") or ""),
        }
    except Exception:
        return {"p256dh": "", "auth": ""}


def subscription_to_public(row: dict[str, Any]) -> dict[str, Any]:
    endpoint = row.get("endpoint") or ""
    return {
        "id": row["id"],
        "channel_id": row["channel_id"],
        "endpoint_masked": mask_secret(endpoint, visible=12),
        "user_agent": row.get("user_agent") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def count_subscriptions(channel_id: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM push_subscriptions WHERE channel_id = ?",
            (channel_id,),
        )
        row = cursor.fetchone()
        if isinstance(row, dict):
            return int(list(row.values())[0])
        return int(row[0])
    finally:
        conn.close()


def list_subscriptions(*, channel_id: str | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if channel_id:
            cursor.execute(
                "SELECT * FROM push_subscriptions WHERE channel_id = ? ORDER BY created_at DESC",
                (channel_id,),
            )
        else:
            cursor.execute("SELECT * FROM push_subscriptions ORDER BY created_at DESC")
        return [subscription_to_public(_row_to_dict(r)) for r in cursor.fetchall()]
    finally:
        conn.close()


def list_subscriptions_decrypted(channel_id: str) -> list[dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM push_subscriptions WHERE channel_id = ?",
            (channel_id,),
        )
        out: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            data = _row_to_dict(row)
            keys = _keys_from_encrypted(data.get("keys_encrypted") or "")
            if not data.get("endpoint") or not keys.get("p256dh") or not keys.get("auth"):
                continue
            out.append({
                "id": data["id"],
                "channel_id": data["channel_id"],
                "endpoint": data["endpoint"],
                "keys": keys,
            })
        return out
    finally:
        conn.close()


def upsert_subscription(
    *,
    channel_id: str,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str | None = None,
) -> dict[str, Any]:
    endpoint = (endpoint or "").strip()
    if not endpoint or not p256dh or not auth:
        raise ValueError("endpoint, p256dh, and auth are required")

    conn = get_connection()
    cursor = conn.cursor()
    now = _now()
    keys_enc = encrypt_config({"p256dh": p256dh, "auth": auth})
    try:
        cursor.execute("SELECT id FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        existing = cursor.fetchone()
        if existing:
            sub_id = existing["id"] if isinstance(existing, dict) else existing[0]
            cursor.execute(
                """
                UPDATE push_subscriptions
                SET channel_id = ?, keys_encrypted = ?, user_agent = ?, updated_at = ?
                WHERE endpoint = ?
                """,
                (channel_id, keys_enc, user_agent or "", now, endpoint),
            )
        else:
            sub_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO push_subscriptions (
                    id, channel_id, endpoint, keys_encrypted, user_agent, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sub_id, channel_id, endpoint, keys_enc, user_agent or "", now, now),
            )
        conn.commit()
    finally:
        conn.close()

    for sub in list_subscriptions(channel_id=channel_id):
        if sub.get("endpoint_masked") == mask_secret(endpoint, visible=12):
            return sub
    return subscription_to_public({
        "id": sub_id,
        "channel_id": channel_id,
        "endpoint": endpoint,
        "user_agent": user_agent or "",
        "created_at": now,
        "updated_at": now,
    })


def delete_subscription(*, subscription_id: str | None = None, endpoint: str | None = None) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if subscription_id:
            cursor.execute("DELETE FROM push_subscriptions WHERE id = ?", (subscription_id,))
        elif endpoint:
            cursor.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint.strip(),))
        else:
            return False
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def delete_subscriptions_for_channel(channel_id: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM push_subscriptions WHERE channel_id = ?", (channel_id,))
        n = cursor.rowcount
        conn.commit()
        return n
    finally:
        conn.close()
