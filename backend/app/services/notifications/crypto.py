"""Encrypt/decrypt per-channel secrets at rest (Fernet + env master key)."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _master_key() -> bytes:
    from app.config import NOTIFICATION_ENCRYPTION_KEY

    raw = (NOTIFICATION_ENCRYPTION_KEY or "").strip()
    if not raw:
        raise ValueError(
            "NOTIFICATION_ENCRYPTION_KEY is not set — required to store notification channel secrets."
        )
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def encrypt_config(config: dict[str, Any]) -> str:
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError(
            "cryptography package required for notification secret encryption "
            "(pip install cryptography)"
        ) from exc
    token = Fernet(_master_key()).encrypt(json.dumps(config).encode("utf-8"))
    return token.decode("utf-8")


def decrypt_config(blob: str) -> dict[str, Any]:
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError("cryptography package required") from exc
    if not blob:
        return {}
    plain = Fernet(_master_key()).decrypt(blob.encode("utf-8"))
    data = json.loads(plain.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def mask_secret(value: str | None, *, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "••••"
    return f"{value[:visible]}…{value[-visible:]}"
