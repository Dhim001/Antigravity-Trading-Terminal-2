"""Webhook delivery — Slack, Discord, and generic JSON presets."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

from app.config import NOTIFICATION_DELIVERY_MAX_RETRIES
from app.services.notifications.events import NotificationEvent

logger = logging.getLogger(__name__)

_RETRY_DELAYS = (1.0, 3.0, 8.0)


def _format_slack(event: NotificationEvent) -> dict[str, Any]:
    emoji = {"info": "ℹ️", "warn": "⚠️", "error": "🚨", "success": "✅"}.get(
        event.severity, "ℹ️"
    )
    lines = [f"*{emoji} {event.title}*", event.body]
    if event.symbol:
        lines.append(f"*Symbol:* `{event.symbol}`")
    if event.bot_id:
        lines.append(f"*Bot:* `{event.bot_id[:8]}…`")
    return {"text": "\n".join(lines)}


def _format_discord(event: NotificationEvent) -> dict[str, Any]:
    color = {"info": 3447003, "warn": 16776960, "error": 15158332, "success": 3066993}.get(
        event.severity, 3447003
    )
    fields = []
    if event.symbol:
        fields.append({"name": "Symbol", "value": event.symbol, "inline": True})
    if event.bot_id:
        fields.append({"name": "Bot", "value": event.bot_id[:12], "inline": True})
    fields.append({"name": "Type", "value": event.event_type, "inline": True})
    return {
        "embeds": [
            {
                "title": event.title,
                "description": event.body[:2000],
                "color": color,
                "fields": fields,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(event.timestamp)),
            }
        ]
    }


def _format_generic(event: NotificationEvent) -> dict[str, Any]:
    return {
        "event_type": event.event_type,
        "severity": event.severity,
        "title": event.title,
        "body": event.body,
        "symbol": event.symbol,
        "bot_id": event.bot_id,
        "payload": event.payload,
        "timestamp": event.timestamp,
    }


def build_webhook_payload(event: NotificationEvent, preset: str) -> dict[str, Any]:
    p = (preset or "generic").lower()
    if p == "slack":
        return _format_slack(event)
    if p == "discord":
        return _format_discord(event)
    return _format_generic(event)


def _sign_body(body: bytes, secret: str) -> str:
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


async def deliver_webhook(event: NotificationEvent, config: dict[str, Any]) -> None:
    url = (config.get("url") or "").strip()
    if not url:
        raise ValueError("Webhook URL is required")
    preset = config.get("preset") or "generic"
    payload = build_webhook_payload(event, preset)
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "TradingTerminal/1.0"}
    hmac_secret = (config.get("hmac_secret") or "").strip()
    if hmac_secret:
        headers["X-Terminal-Signature"] = _sign_body(body, hmac_secret)

    last_exc: Exception | None = None
    for attempt in range(NOTIFICATION_DELIVERY_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, content=body, headers=headers)
                if resp.status_code == 429:
                    delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                    await _async_sleep(delay * 2)
                    continue
                resp.raise_for_status()
                return
        except Exception as exc:
            last_exc = exc
            if attempt < NOTIFICATION_DELIVERY_MAX_RETRIES - 1:
                await _async_sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])
    raise last_exc or RuntimeError("Webhook delivery failed")


async def _async_sleep(sec: float) -> None:
    import asyncio
    await asyncio.sleep(sec)
