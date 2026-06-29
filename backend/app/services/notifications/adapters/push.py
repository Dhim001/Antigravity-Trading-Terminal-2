"""Web Push delivery via pywebpush."""

from __future__ import annotations

import asyncio
import json
import logging

from app.config import VAPID_PRIVATE_KEY, VAPID_SUBJECT
from app.services.notifications.events import NotificationEvent
from app.services.notifications.push_subscriptions import (
    delete_subscription,
    list_subscriptions_decrypted,
)
from app.services.notifications.vapid import web_push_configured

logger = logging.getLogger(__name__)


def _build_payload(event: NotificationEvent) -> str:
    return json.dumps({
        "title": event.title,
        "body": event.body,
        "event_type": event.event_type,
        "severity": event.severity,
        "symbol": event.symbol,
        "bot_id": event.bot_id,
        "payload": event.payload or {},
    })


def _send_one(subscription: dict, data: str) -> None:
    from pywebpush import WebPushException, webpush

    webpush(
        subscription_info={
            "endpoint": subscription["endpoint"],
            "keys": subscription["keys"],
        },
        data=data,
        vapid_private_key=VAPID_PRIVATE_KEY,
        vapid_claims={"sub": VAPID_SUBJECT},
        timeout=20,
    )


async def deliver_push(event: NotificationEvent, channel: dict) -> None:
    if not web_push_configured():
        raise RuntimeError("Web Push is not configured (VAPID keys missing)")

    channel_id = channel.get("id")
    if not channel_id:
        raise ValueError("push channel id is required")

    subs = await asyncio.to_thread(list_subscriptions_decrypted, channel_id)
    if not subs:
        raise RuntimeError("No browser subscriptions for this push channel")

    data = _build_payload(event)
    errors: list[str] = []
    sent = 0

    for sub in subs:
        try:
            await asyncio.to_thread(_send_one, sub, data)
            sent += 1
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(exc, "response", None)
            code = None
            if hasattr(exc, "response") and exc.response is not None:
                code = getattr(exc.response, "status_code", None)
            try:
                from pywebpush import WebPushException
                if isinstance(exc, WebPushException) and exc.response is not None:
                    code = exc.response.status_code
            except Exception:
                pass
            if code in (404, 410):
                await asyncio.to_thread(delete_subscription, endpoint=sub["endpoint"])
                logger.info("Removed expired push subscription %s", sub["id"])
            errors.append(str(exc))

    if sent == 0:
        raise RuntimeError(errors[0] if errors else "Push delivery failed")
