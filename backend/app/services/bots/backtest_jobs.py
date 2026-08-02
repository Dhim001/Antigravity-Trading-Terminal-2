"""In-process backtest job tokens — cooperative cancel per WebSocket client + job_id."""

from __future__ import annotations

import threading
from typing import Any


class _BacktestJob:
    __slots__ = ("cancelled", "job_id", "deferred")

    def __init__(self, job_id: str | None = None, deferred: bool = False) -> None:
        self.cancelled = False
        self.job_id = job_id
        self.deferred = deferred

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


def start_job(websocket: Any, job_id: str | None = None, *, deferred: bool = False) -> _BacktestJob | None:
    key = _client_key(websocket)
    if key is None:
        return _BacktestJob(job_id=job_id, deferred=deferred)
    job = _BacktestJob(job_id=job_id, deferred=deferred)
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


def cancel_job_by_id(job_id: str | None) -> bool:
    """Flip the in-memory token for ``job_id`` when this process owns the run.

    The run also polls the persisted status, but cancelling the token stops it
    on the very next bar without depending on a DB read.
    """
    if not job_id:
        return False
    with _lock:
        key = _job_id_to_client.get(job_id)
        job = _jobs.get(key) if key is not None else None
    if job is None:
        return False
    job.cancel()
    return True


def clear_job(websocket: Any) -> None:
    key = _client_key(websocket)
    if key is None:
        return
    with _lock:
        job = _jobs.pop(key, None)
        if job and job.job_id:
            _job_id_to_client.pop(job.job_id, None)


def abandon_client_jobs(websocket: Any) -> bool:
    """Release a disconnecting client's run.

    Inline runs are cancelled (nobody is left to receive the result), but
    deferred jobs keep going: their results are persisted to ``backtest_jobs``
    and any client can reattach via the session snapshot. Cancelling those on a
    tab reload used to throw away long ML/RL runs mid-flight.
    """
    key = _client_key(websocket)
    if key is None:
        return False
    with _lock:
        job = _jobs.pop(key, None)
        if job and job.job_id:
            _job_id_to_client.pop(job.job_id, None)
    if not job or job.deferred:
        return False
    job.cancel()
    if job.job_id:
        from app.services.bots.backtest_job_store import request_cancel_job

        request_cancel_job(job.job_id)
    return True
