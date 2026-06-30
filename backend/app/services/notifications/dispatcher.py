"""Fan-out notification events to configured channels."""

from __future__ import annotations

import asyncio
import logging
import uuid

from app.config import NOTIFICATIONS_ENABLED
from app.services.notifications import types as ntypes
from app.services.notifications.dedupe import make_dedupe_key
from app.services.notifications.events import NotificationEvent
from app.services.notifications.store import (
    list_enabled_channels,
    mark_delivery,
    try_claim_delivery,
)

logger = logging.getLogger(__name__)


def _channel_wants_event(channel: dict, event_type: str) -> bool:
    types = channel.get("event_types") or []
    if not types or "*" in types:
        return True
    return event_type in types


async def _deliver_to_channel(
    event: NotificationEvent,
    channel: dict,
    log_id: str,
) -> None:
    from app.services.notifications.adapters.email import deliver_email
    from app.services.notifications.adapters.push import deliver_push
    from app.services.notifications.adapters.telegram import deliver_telegram
    from app.services.notifications.adapters.webhook import deliver_webhook

    ctype = channel.get("channel_type")
    config = channel.get("config") or {}
    try:
        if ctype == ntypes.CHANNEL_WEBHOOK:
            await deliver_webhook(event, config)
        elif ctype == ntypes.CHANNEL_TELEGRAM:
            await deliver_telegram(event, config)
        elif ctype == ntypes.CHANNEL_EMAIL:
            await deliver_email(event, config)
        elif ctype == ntypes.CHANNEL_PUSH:
            await deliver_push(event, channel)
        else:
            raise ValueError(f"Unsupported channel type: {ctype}")
        mark_delivery(log_id, status="sent")
    except Exception as exc:
        logger.warning(
            "Notification delivery failed channel=%s event=%s: %s",
            channel.get("id"),
            event.event_type,
            exc,
        )
        mark_delivery(log_id, status="failed", error=str(exc))


async def emit_notification(
    event: NotificationEvent,
    *,
    channel_ids: list[str] | None = None,
) -> int:
    """Dispatch event to matching enabled channels. Returns delivery task count."""
    if not NOTIFICATIONS_ENABLED:
        return 0
    try:
        channels = list_enabled_channels()
    except Exception as exc:
        logger.debug("Notification channels unavailable: %s", exc)
        return 0
    if not channels:
        return 0

    if channel_ids is not None:
        if not channel_ids:
            return 0
        allow = set(channel_ids)
        channels = [c for c in channels if c.get("id") in allow]

    queued = 0
    for channel in channels:
        if not _channel_wants_event(channel, event.event_type):
            continue
        channel_id = channel["id"]
        dedupe_key = make_dedupe_key(event, channel_id)
        log_id = str(uuid.uuid4())
        claimed = await asyncio.to_thread(
            try_claim_delivery,
            log_id=log_id,
            dedupe_key=dedupe_key,
            channel_id=channel_id,
            event_type=event.event_type,
            payload=event.to_dict(),
        )
        if not claimed:
            continue
        asyncio.create_task(_deliver_to_channel(event, channel, log_id))
        queued += 1
    return queued


def classify_bot_log(level: str, message: str) -> tuple[str | None, str]:
    """Map bot log lines to notification event types. Returns (type, severity)."""
    msg = (message or "").lower()
    if any(k in msg for k in ("sl/tp", "stop-loss", "take-profit", "take profit", "stop loss")):
        return ntypes.SL_TP_TRIGGER, "warn"
    if "pnl" in msg and ("exit" in msg or "closed" in msg):
        return ntypes.SL_TP_TRIGGER, "info"
    if level == "ERROR":
        return ntypes.BOT_LOG_ERROR, "error"
    if level == "WARN":
        return ntypes.BOT_LOG_WARN, "warn"
    if any(k in msg for k in ("paused", "resumed", "stopped", "created", "kill switch", "safe mode")):
        return ntypes.BOT_STATUS, "info"
    if any(k in msg for k in ("filled", "fill ", "order placed", "market buy", "market sell")):
        return ntypes.TRADE_FILL, "success"
    return None, "info"


async def notify_bot_log(
    bot_id: str,
    level: str,
    message: str,
    *,
    symbol: str | None = None,
    meta: dict | None = None,
) -> None:
    event_type, severity = classify_bot_log(level, message)
    if not event_type:
        return
    if meta and meta.get("event_type"):
        event_type = str(meta["event_type"])
    sym = symbol or (meta or {}).get("symbol")
    await emit_notification(
        NotificationEvent(
            event_type=event_type,
            title=f"Bot {event_type.replace('_', ' ')}",
            body=message,
            severity=severity,
            symbol=sym,
            bot_id=bot_id if bot_id != "system" else None,
            payload=meta or {},
        )
    )
