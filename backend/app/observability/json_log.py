"""Structured JSON logging helpers for trade and agent paths."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("symbol", "bot_id", "insight_id", "action", "duration_ms", "event"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    extra = {k: v for k, v in fields.items() if v is not None}
    extra["event"] = event
    logger.info(event, extra=extra)
