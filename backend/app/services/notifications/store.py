"""Notification channel + delivery log persistence."""

from __future__ import annotations

import json
import logging
import secrets
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

from app.db.connection import get_connection, is_postgres
from app.services.notifications import types as ntypes
from app.services.notifications.crypto import decrypt_config, encrypt_config, mask_secret


def _now() -> float:
    return time.time()


def _row_to_dict(row) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    cols = (
        "id", "channel_type", "name", "enabled", "event_types",
        "config_encrypted", "created_at", "updated_at",
    )
    return {cols[i]: row[i] for i in range(min(len(row), len(cols)))}


def _parse_event_types(raw) -> list[str]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return []
    return []


def validate_channel_config(channel_type: str, config: dict[str, Any]) -> None:
    if channel_type == ntypes.CHANNEL_WEBHOOK:
        if not (config.get("url") or "").strip():
            raise ValueError("url is required for webhook channels")
    elif channel_type == ntypes.CHANNEL_TELEGRAM:
        if not (config.get("bot_token") or "").strip():
            raise ValueError("bot_token is required for Telegram channels")
        if not (config.get("chat_id") or "").strip():
            raise ValueError("chat_id is required for Telegram channels")
    elif channel_type == ntypes.CHANNEL_EMAIL:
        if not (config.get("smtp_host") or "").strip():
            raise ValueError("smtp_host is required for email channels")
        from_addr = (config.get("from_address") or config.get("smtp_user") or "").strip()
        if not from_addr:
            raise ValueError("from_address or smtp_user is required for email channels")
        to_raw = config.get("to_addresses") or config.get("to_address") or []
        if isinstance(to_raw, str):
            to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]
        else:
            to_addrs = [str(a).strip() for a in to_raw if str(a).strip()]
        if not to_addrs:
            raise ValueError("to_addresses is required for email channels")
    elif channel_type == ntypes.CHANNEL_PUSH:
        return
    else:
        raise ValueError(f"Unsupported channel type: {channel_type}")


def channel_to_public(row: dict[str, Any]) -> dict[str, Any]:
    """API-safe channel view — secrets masked."""
    try:
        cfg = decrypt_config(row.get("config_encrypted") or "")
    except Exception:
        cfg = {}
    event_types = _parse_event_types(row.get("event_types"))
    channel_type = row.get("channel_type", ntypes.CHANNEL_WEBHOOK)
    out: dict[str, Any] = {
        "id": row["id"],
        "channel_type": channel_type,
        "name": row["name"],
        "enabled": bool(row.get("enabled")),
        "event_types": event_types or [],
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    if channel_type == ntypes.CHANNEL_WEBHOOK:
        out["preset"] = cfg.get("preset", "generic")
        url = cfg.get("url", "")
        out["url_masked"] = mask_secret(url, visible=8) if url else ""
        out["has_hmac_secret"] = bool(cfg.get("hmac_secret"))
    elif channel_type == ntypes.CHANNEL_TELEGRAM:
        out["chat_id_masked"] = mask_secret(str(cfg.get("chat_id", "")), visible=4)
        out["bot_token_masked"] = mask_secret(str(cfg.get("bot_token", "")), visible=6)
        out["parse_mode"] = cfg.get("parse_mode") or "MarkdownV2"
    elif channel_type == ntypes.CHANNEL_EMAIL:
        out["smtp_host"] = cfg.get("smtp_host", "")
        out["smtp_port"] = int(cfg.get("smtp_port") or 587)
        out["from_address"] = cfg.get("from_address") or cfg.get("smtp_user") or ""
        to_raw = cfg.get("to_addresses") or cfg.get("to_address") or []
        if isinstance(to_raw, str):
            to_list = [a.strip() for a in to_raw.split(",") if a.strip()]
        else:
            to_list = [str(a).strip() for a in to_raw if str(a).strip()]
        out["to_addresses"] = [mask_secret(a, visible=3) for a in to_list]
        out["has_smtp_password"] = bool(cfg.get("smtp_password"))
        out["use_tls"] = bool(cfg.get("use_tls", True))
    elif channel_type == ntypes.CHANNEL_PUSH:
        from app.services.notifications.push_subscriptions import count_subscriptions
        out["subscription_count"] = count_subscriptions(row["id"])
        out["has_subscribe_secret"] = bool(cfg.get("subscribe_secret"))
    return out


def list_channels(*, channel_type: str | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if channel_type:
            cursor.execute(
                "SELECT * FROM notification_channels WHERE channel_type = ? ORDER BY name",
                (channel_type,),
            )
        else:
            cursor.execute("SELECT * FROM notification_channels ORDER BY name")
        return [channel_to_public(_row_to_dict(r)) for r in cursor.fetchall()]
    finally:
        conn.close()


def get_channel(channel_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM notification_channels WHERE id = ?", (channel_id,))
        row = cursor.fetchone()
        return channel_to_public(_row_to_dict(row)) if row else None
    finally:
        conn.close()


def get_channel_decrypted(channel_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM notification_channels WHERE id = ?", (channel_id,))
        row = cursor.fetchone()
        if not row:
            return None
        data = _row_to_dict(row)
        event_types = data.get("event_types")
        if isinstance(event_types, str):
            event_types = json.loads(event_types)
        data["event_types"] = event_types or []
        data["config"] = decrypt_config(data.get("config_encrypted") or "")
        return data
    finally:
        conn.close()


def list_enabled_channels() -> list[dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM notification_channels WHERE enabled = 1 ORDER BY name"
        )
        out: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            data = _row_to_dict(row)
            event_types = data.get("event_types")
            if isinstance(event_types, str):
                event_types = json.loads(event_types)
            data["event_types"] = event_types or []
            try:
                data["config"] = decrypt_config(data.get("config_encrypted") or "")
            except Exception as exc:
                logger.warning(
                    "Skipping notification channel %s (%s): config decrypt failed: %s",
                    data.get("id"),
                    data.get("name"),
                    exc,
                )
                continue
            out.append(data)
        return out
    finally:
        conn.close()


def upsert_channel(
    *,
    channel_id: str | None,
    channel_type: str,
    name: str,
    enabled: bool,
    event_types: list[str],
    config: dict[str, Any],
) -> dict[str, Any]:
    if channel_type not in ntypes.ALL_CHANNEL_TYPES:
        raise ValueError(f"Unsupported channel type: {channel_type}")
    validate_channel_config(channel_type, config)
    if channel_type == ntypes.CHANNEL_PUSH and not (config.get("subscribe_secret") or "").strip():
        config["subscribe_secret"] = secrets.token_urlsafe(32)
    cid = channel_id or str(uuid.uuid4())
    now = _now()
    enc = encrypt_config(config)
    types_json = json.dumps(event_types or list(ntypes.ALL_EVENT_TYPES))

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM notification_channels WHERE id = ?", (cid,))
        exists = cursor.fetchone() is not None
        if exists:
            cursor.execute(
                """
                UPDATE notification_channels
                SET channel_type = ?, name = ?, enabled = ?, event_types = ?,
                    config_encrypted = ?, updated_at = ?
                WHERE id = ?
                """,
                (channel_type, name, int(enabled), types_json, enc, now, cid),
            )
        else:
            cursor.execute(
                """
                INSERT INTO notification_channels (
                    id, channel_type, name, enabled, event_types, config_encrypted, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (cid, channel_type, name, int(enabled), types_json, enc, now, now),
            )
        conn.commit()
    finally:
        conn.close()
    row = get_channel(cid)
    return row or {"id": cid}


def delete_channel(channel_id: str) -> bool:
    from app.services.notifications.push_subscriptions import delete_subscriptions_for_channel

    delete_subscriptions_for_channel(channel_id)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM notification_channels WHERE id = ?", (channel_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def try_claim_delivery(
    *,
    log_id: str,
    dedupe_key: str,
    channel_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> bool:
    """Insert pending log row; return False if dedupe already claimed (distributed-safe)."""
    conn = get_connection()
    cursor = conn.cursor()
    now = _now()
    try:
        if is_postgres():
            cursor.execute(
                """
                INSERT INTO notification_log (
                    id, dedupe_key, channel_id, event_type, status, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                ON CONFLICT (dedupe_key, channel_id) DO NOTHING
                """,
                (log_id, dedupe_key, channel_id, event_type, json.dumps(payload), now),
            )
            claimed = cursor.rowcount > 0
        else:
            try:
                cursor.execute(
                    """
                    INSERT INTO notification_log (
                        id, dedupe_key, channel_id, event_type, status, payload_json, created_at
                    )
                    VALUES (?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (log_id, dedupe_key, channel_id, event_type, json.dumps(payload), now),
                )
                claimed = True
            except Exception:
                claimed = False
        conn.commit()
        return claimed
    finally:
        conn.close()


def mark_delivery(log_id: str, *, status: str, error: str | None = None) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE notification_log SET status = ?, error = ?, delivered_at = ?
            WHERE id = ?
            """,
            (status, error, _now(), log_id),
        )
        conn.commit()
    finally:
        conn.close()
