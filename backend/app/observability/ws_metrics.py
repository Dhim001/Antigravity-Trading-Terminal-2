"""WebSocket client connect/disconnect metrics for /health and Prometheus."""

from __future__ import annotations

import threading
import time
from collections import deque

from app.observability.metrics import inc

_lock = threading.Lock()
_connects_total = 0
_disconnects_by_code: dict[int, int] = {}
_recent: deque[dict] = deque(maxlen=10)
_last_disconnect: dict | None = None


def _category(code: int) -> str:
    if code in (1000, 1001):
        return "normal"
    if code in (1011, 4000):
        return "timeout"
    if code == 1006:
        return "abnormal"
    if 4000 <= code < 5000:
        return "application"
    return "other"


def record_ws_connect() -> None:
    global _connects_total
    with _lock:
        _connects_total += 1
    inc("ws_client_connects_total")


def record_ws_disconnect(code: int | None, reason: str | None = None) -> None:
    global _last_disconnect
    normalized = int(code) if code is not None else 1006
    reason_s = (reason or "")[:200]
    category = _category(normalized)
    ts = time.time()
    entry = {
        "code": normalized,
        "reason": reason_s,
        "category": category,
        "at": ts,
    }
    with _lock:
        _disconnects_by_code[normalized] = _disconnects_by_code.get(normalized, 0) + 1
        _last_disconnect = entry
        _recent.appendleft(entry)
    inc(
        "ws_client_disconnects_total",
        labels={"code": str(normalized), "category": category},
    )


def ws_metrics_snapshot(*, connected: int = 0) -> dict:
    """Structured WS client metrics for /health and admin UI."""
    with _lock:
        by_code = {str(k): v for k, v in sorted(_disconnects_by_code.items())}
        disconnects_total = sum(_disconnects_by_code.values())
        last = dict(_last_disconnect) if _last_disconnect else None
        if last:
            last["at"] = round(last["at"], 3)
        recent = [{**e, "at": round(e["at"], 3)} for e in list(_recent)]
        connects = _connects_total
    return {
        "connected": connected,
        "connects_total": connects,
        "disconnects_total": disconnects_total,
        "by_code": by_code,
        "last_disconnect": last,
        "recent": recent,
    }
