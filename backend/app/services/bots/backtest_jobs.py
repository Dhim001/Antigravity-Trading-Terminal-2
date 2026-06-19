"""In-process backtest job tokens — cooperative cancel per WebSocket client."""

from __future__ import annotations

import threading
from typing import Any


class _BacktestJob:
    __slots__ = ("cancelled",)

    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def is_cancelled(self) -> bool:
        return self.cancelled


_lock = threading.Lock()
_jobs: dict[int, _BacktestJob] = {}


def _client_key(websocket: Any) -> int | None:
    if websocket is None:
        return None
    return id(websocket)


def start_job(websocket: Any) -> _BacktestJob | None:
    key = _client_key(websocket)
    if key is None:
        return None
    job = _BacktestJob()
    with _lock:
        old = _jobs.get(key)
        if old:
            old.cancel()
        _jobs[key] = job
    return job


def get_job(websocket: Any) -> _BacktestJob | None:
    key = _client_key(websocket)
    if key is None:
        return None
    with _lock:
        return _jobs.get(key)


def cancel_job(websocket: Any) -> bool:
    job = get_job(websocket)
    if not job:
        return False
    job.cancel()
    return True


def clear_job(websocket: Any) -> None:
    key = _client_key(websocket)
    if key is None:
        return
    with _lock:
        _jobs.pop(key, None)
