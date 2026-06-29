"""Telegram Bot API delivery."""

from __future__ import annotations

import logging

import httpx

from app.services.notifications.events import NotificationEvent

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _escape_md(text: str) -> str:
    """Minimal MarkdownV2 escape for Telegram."""
    for ch in ("_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(ch, f"\\{ch}")
    return text


def format_telegram_message(event: NotificationEvent) -> str:
    lines = [f"*{_escape_md(event.title)}*", _escape_md(event.body)]
    if event.symbol:
        lines.append(f"Symbol: `{event.symbol}`")
    if event.bot_id:
        lines.append(f"Bot: `{event.bot_id[:12]}`")
    lines.append(f"Type: `{event.event_type}`")
    return "\n".join(lines)


async def deliver_telegram(event: NotificationEvent, config: dict) -> None:
    token = (config.get("bot_token") or "").strip()
    chat_id = (config.get("chat_id") or "").strip()
    if not token or not chat_id:
        raise ValueError("Telegram bot_token and chat_id are required")

    parse_mode = (config.get("parse_mode") or "MarkdownV2").strip()
    text = format_telegram_message(event) if parse_mode == "MarkdownV2" else (
        f"{event.title}\n{event.body}"
        + (f"\nSymbol: {event.symbol}" if event.symbol else "")
        + (f"\nBot: {event.bot_id}" if event.bot_id else "")
    )

    url = _TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": text[:4096],
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, json=payload)
        data = resp.json()
        if not resp.is_success or not data.get("ok"):
            desc = data.get("description") or resp.text
            raise RuntimeError(f"Telegram API error: {desc}")
