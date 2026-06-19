"""In-process backtest job tokens — cooperative cancel per WebSocket client + job_id."""

from __future__ import annotations

import threading
from typing import Any


class _BacktestJob:
    __slots__ = ("cancelled", "job_id")

    def __init__(self, job_id: str | None = None) -> None:
        self.cancelled = False
        self.job_id = job_id

    def cancel(self) -> None:
        self.cancelled = True

    def is_cancelled(self) -> bool:
        return self.cancelled


_lock = threading.Lock()
_jobs: dict[int, _BacktestJob] = {}
_job_id_to_client: dict[str, int] = {}


def _client_key(websocket: Any) -> int | None:
    if websocket is None:
        return None
    return id(websocket)


def start_job(websocket: Any, job_id: str | None = None) -> _BacktestJob | None:
    key = _client_key(websocket)
    if key is None:
        return _BacktestJob(job_id=job_id)
    job = _BacktestJob(job_id=job_id)
    with _lock:
        old = _jobs.get(key)
        if old:
            old.cancel()
        _jobs[key] = job
        if job_id:
            _job_id_to_client[job_id] = key
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
    if job.job_id:
        from app.services.bots.backtest_job_store import request_cancel_job
        request_cancel_job(job.job_id)
    return True


def clear_job(websocket: Any) -> None:
    key = _client_key(websocket)
    if key is None:
        return
    with _lock:
        job = _jobs.pop(key, None)
        if job and job.job_id:
            _job_id_to_client.pop(job.job_id, None)
