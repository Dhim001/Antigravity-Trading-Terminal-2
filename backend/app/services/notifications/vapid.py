"""Web Push VAPID configuration."""

from __future__ import annotations

from app.config import (
    VAPID_PRIVATE_KEY,
    VAPID_PUBLIC_KEY,
    VAPID_SUBJECT,
    WEB_PUSH_ENABLED,
)


def web_push_configured() -> bool:
    return bool(
        WEB_PUSH_ENABLED
        and VAPID_PUBLIC_KEY
        and VAPID_PRIVATE_KEY
        and VAPID_SUBJECT
    )


def get_vapid_public_key() -> str | None:
    if not web_push_configured():
        return None
    return VAPID_PUBLIC_KEY
