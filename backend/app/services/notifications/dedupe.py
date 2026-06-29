"""Dedupe keys for distributed notification delivery."""

from __future__ import annotations

import hashlib

from app.config import NOTIFICATION_DEDUPE_WINDOW_SEC
from app.services.notifications.events import NotificationEvent


def make_dedupe_key(event: NotificationEvent, channel_id: str) -> str:
    if event.event_type == "daily_digest":
        digest_date = (event.payload or {}).get("digest_date", "")
        parts = [event.event_type, channel_id, str(digest_date)]
    elif event.event_type == "alert_rule":
        payload = event.payload or {}
        parts = [
            event.event_type,
            channel_id,
            str(payload.get("rule_id", "")),
            str(payload.get("bar_time", "")),
        ]
    else:
        bucket = int(event.timestamp // NOTIFICATION_DEDUPE_WINDOW_SEC)
        parts = [
            event.event_type,
            channel_id,
            event.bot_id or "",
            event.symbol or "",
            event.title,
            str(bucket),
        ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]
