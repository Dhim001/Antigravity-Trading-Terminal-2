"""Email delivery via stdlib smtplib."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.services.notifications.events import NotificationEvent

logger = logging.getLogger(__name__)


def _send_smtp(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    use_tls: bool,
    from_addr: str,
    to_addrs: list[str],
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if use_tls:
            smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.sendmail(from_addr, to_addrs, msg.as_string())


async def deliver_email(event: NotificationEvent, config: dict) -> None:
    import asyncio

    host = (config.get("smtp_host") or "").strip()
    port = int(config.get("smtp_port") or 587)
    user = (config.get("smtp_user") or "").strip()
    password = (config.get("smtp_password") or "").strip()
    use_tls = bool(config.get("use_tls", True))
    from_addr = (config.get("from_address") or user or "").strip()
    to_raw = config.get("to_addresses") or config.get("to_address") or []
    if isinstance(to_raw, str):
        to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]
    else:
        to_addrs = [str(a).strip() for a in to_raw if str(a).strip()]

    if not host or not from_addr or not to_addrs:
        raise ValueError("smtp_host, from_address, and to_addresses are required")

    body_text = event.body
    if event.symbol:
        body_text += f"\n\nSymbol: {event.symbol}"
    if event.bot_id:
        body_text += f"\nBot: {event.bot_id}"

    await asyncio.to_thread(
        _send_smtp,
        host=host,
        port=port,
        user=user,
        password=password,
        use_tls=use_tls,
        from_addr=from_addr,
        to_addrs=to_addrs,
        subject=event.title,
        body_text=body_text,
        body_html=(event.payload or {}).get("html_body"),
    )
