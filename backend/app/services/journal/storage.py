"""Trade journal screenshot storage — local data-URL / file with Cloudinary-ready adapter seam."""

from __future__ import annotations

import base64
import os
import re
import uuid

from app.services.synthetic_data import DATA_DIR

JOURNAL_SCREENSHOT_DIR = os.path.join(DATA_DIR, "journal_screenshots")
MAX_DATA_URL_BYTES = 120_000  # ~120 KB — larger payloads saved to disk

_DATA_URL_RE = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", re.DOTALL)


class ScreenshotStorage:
    """Pluggable storage — swap LocalScreenshotStorage for Cloudinary later."""

    def save(self, payload: str | None) -> str | None:
        raise NotImplementedError


class LocalScreenshotStorage(ScreenshotStorage):
    """Persist screenshots as data-URLs (small) or local files (large)."""

    def __init__(self, base_dir: str | None = None):
        self.base_dir = base_dir or JOURNAL_SCREENSHOT_DIR
        os.makedirs(self.base_dir, exist_ok=True)

    def save(self, payload: str | None) -> str | None:
        if not payload or not isinstance(payload, str):
            return None
        payload = payload.strip()
        if not payload:
            return None

        match = _DATA_URL_RE.match(payload)
        if not match:
            # Already a stored reference (file: or http)
            return payload

        mime, b64 = match.group(1), match.group(2)
        try:
            raw = base64.b64decode(b64, validate=True)
        except (ValueError, TypeError):
            return None

        ext = "png" if "png" in mime else "jpg" if "jpeg" in mime or "jpg" in mime else "webp"
        if len(raw) <= MAX_DATA_URL_BYTES:
            return payload

        name = f"{uuid.uuid4().hex}.{ext}"
        path = os.path.join(self.base_dir, name)
        with open(path, "wb") as fh:
            fh.write(raw)
        return f"file:journal/{name}"


_default_storage: ScreenshotStorage | None = None


def get_screenshot_storage() -> ScreenshotStorage:
    global _default_storage
    if _default_storage is None:
        _default_storage = LocalScreenshotStorage()
    return _default_storage


def resolve_screenshot_url(stored: str | None) -> str | None:
    """Resolve stored ref to a displayable URL (data-URL or file: ref for client)."""
    if not stored:
        return None
    if stored.startswith("data:") or stored.startswith("http"):
        return stored
    if stored.startswith("file:journal/"):
        name = stored.split("/", 1)[1]
        path = os.path.join(JOURNAL_SCREENSHOT_DIR, name)
        if not os.path.isfile(path):
            return stored
        ext = name.rsplit(".", 1)[-1].lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext, "image/png")
        with open(path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    return stored
