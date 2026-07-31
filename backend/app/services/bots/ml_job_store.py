"""In-memory ML train/validate job store (ML_LAB_IMPROVEMENTS Phase 1).

Statuses: queued | running | done | error | cancelled
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
import uuid
from concurrent.futures import Future
from datetime import datetime, timezone
from typing import Any

_STATUSES = frozenset({"queued", "running", "done", "error", "cancelled"})
_TERMINAL = frozenset({"done", "error", "cancelled"})
_MAX_JOBS = 80

_lock = threading.RLock()
_jobs: dict[str, dict[str, Any]] = {}
_futures: dict[str, Future] = {}

# Headline keys kept in RAM when a large terminal result is offloaded (#31).
# Include Lab Apply & Retrain / sweep fields so a failed disk hydrate still
# leaves enough to retrain and show scores (trial_history stays on disk only).
_SLIM_RESULT_KEYS = (
    "ok", "error", "metrics", "aggregate", "mean_accuracy",
    "n_folds", "pbo", "version_id", "trained_at",
    "best_hyperparams", "best_score", "trials_completed", "max_trials",
    "optimization_run_id", "importance_ranking", "objective_kind",
    "multi_fidelity", "training_window", "timeframe", "symbol", "strategy",
    "early_stopped", "epochs_trained", "epochs_budget",
)


def _result_dir() -> str:
    from app.config import DATA_DIR

    return os.path.join(DATA_DIR, "ml_job_results")


def _result_path(job_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(job_id or "job"))
    return os.path.join(_result_dir(), f"{safe}.json")


def _delete_result_file(job_id: str) -> None:
    try:
        os.remove(_result_path(job_id))
    except OSError:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _prune_locked() -> None:
    if len(_jobs) <= _MAX_JOBS:
        return
    finished = sorted(
        (
            (jid, j)
            for jid, j in _jobs.items()
            if j.get("status") in _TERMINAL
        ),
        key=lambda kv: float(kv[1].get("finished_at_epoch") or 0),
    )
    excess = len(_jobs) - _MAX_JOBS
    for jid, _ in finished[: max(0, excess)]:
        _jobs.pop(jid, None)
        _futures.pop(jid, None)
        _delete_result_file(jid)


def _offload_terminal_result(job_id: str) -> None:
    """Replace a large terminal ``result`` with a slim headline + disk file (#31).

    Walk-forward bundles / metrics trees can be megabytes each; the store caps
    at 80 jobs, so hot results alone could hold hundreds of MB. Results smaller
    than ``ML_JOB_RESULT_OFFLOAD_BYTES`` stay in RAM (no disk churn for typical
    validate jobs). If the file write fails the full result stays in RAM —
    never worse than before.
    """
    from app.config import ML_JOB_RESULT_OFFLOAD_BYTES

    with _lock:
        job = _jobs.get(job_id)
        result = job.get("result") if job else None
        if not isinstance(result, dict) or result.get("_slimmed"):
            return
        safe = {k: v for k, v in result.items() if k != "_wf_bundle"}

    try:
        import json as _json

        payload = _json_safe_ml_value(safe)
        raw = _json.dumps(payload)
    except Exception:
        return
    if len(raw) < ML_JOB_RESULT_OFFLOAD_BYTES:
        return

    try:
        path = _result_path(job_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(raw)
        os.replace(tmp, path)
    except OSError:
        return

    slim = {k: payload.get(k) for k in _SLIM_RESULT_KEYS if payload.get(k) is not None}
    slim["_slimmed"] = True
    slim["_result_file"] = path
    with _lock:
        job = _jobs.get(job_id)
        if job and isinstance(job.get("result"), dict) and not job["result"].get("_slimmed"):
            job["result"] = slim


def _load_offloaded_result(result: dict[str, Any]) -> dict[str, Any] | None:
    """Read back a slimmed result's full payload from its disk file."""
    path = result.get("_result_file")
    if not path:
        return None
    try:
        import json as _json

        with open(path, "r", encoding="utf-8") as fh:
            data = _json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def create_ml_job(
    *,
    kind: str,
    strategy: str,
    symbol: str,
    progress_path: str | None = None,
    job_id: str | None = None,
) -> str:
    """Register a new job; returns job_id."""
    kind_n = str(kind or "train").lower()
    if kind_n not in ("train", "validate", "hyperparam_sweep"):
        kind_n = "train"
    jid = str(job_id) if job_id else str(uuid.uuid4())
    with _lock:
        if jid in _jobs:
            return jid
        _jobs[jid] = {
            "job_id": jid,
            "kind": kind_n,
            "strategy": str(strategy or "").upper(),
            "symbol": str(symbol or "").upper(),
            "status": "queued",
            "started_at": None,
            "finished_at": None,
            "finished_at_epoch": None,
            "created_at": _now_iso(),
            "created_at_epoch": time.time(),
            "progress": {"pct": 0, "phase": "queued", "detail": ""},
            "result": None,
            "error": None,
            "progress_path": progress_path,
            "cancel_requested": False,
        }
        _prune_locked()
    return jid


def get_ml_job(job_id: str) -> dict[str, Any] | None:
    if not job_id:
        return None
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def list_ml_jobs(*, limit: int = 20, active_only: bool = False) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 20), 100))
    with _lock:
        rows = list(_jobs.values())
    rows.sort(key=lambda j: float(j.get("created_at_epoch") or 0), reverse=True)
    if active_only:
        rows = [j for j in rows if j.get("status") in ("queued", "running")]
    return [dict(j) for j in rows[:limit]]


def ml_job_counts() -> dict[str, int]:
    with _lock:
        active = sum(1 for j in _jobs.values() if j.get("status") == "running")
        queued = sum(1 for j in _jobs.values() if j.get("status") == "queued")
    return {"active": active, "queued": queued}


def set_ml_job_progress_path(job_id: str, progress_path: str | None) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job["progress_path"] = progress_path


def attach_ml_job_future(job_id: str, future: Future) -> None:
    if not job_id or future is None:
        return
    with _lock:
        _futures[job_id] = future


def mark_ml_job_running(job_id: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job or job.get("status") in _TERMINAL:
            return
        job["status"] = "running"
        if not job.get("started_at"):
            job["started_at"] = _now_iso()


def update_ml_job_progress(job_id: str, progress: dict[str, Any] | None) -> dict[str, Any] | None:
    if not job_id:
        return None
    with _lock:
        job = _jobs.get(job_id)
        if not job or job.get("status") in _TERMINAL:
            return None
        src = progress if isinstance(progress, dict) else {}
        payload: dict[str, Any] = {
            "pct": int(src.get("pct") or 0),
            "phase": str(src.get("phase") or ""),
            "detail": str(src.get("detail") or ""),
        }
        # Preserve Auto-Tune / extended snapshot fields from the progress file.
        for key, val in src.items():
            if key in ("pct", "phase", "detail", "updated_at"):
                continue
            payload[key] = val
        job["progress"] = payload
        if job.get("status") == "queued":
            job["status"] = "running"
            if not job.get("started_at"):
                job["started_at"] = _now_iso()
        return dict(job)


def finish_ml_job(
    job_id: str,
    status: str,
    *,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    if not job_id or status not in _TERMINAL:
        return
    snapshot: dict[str, Any] | None = None
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        # Already terminal — attach result if useful, never double-persist runs.
        if job.get("status") in _TERMINAL:
            if result is not None and job.get("result") is None:
                job["result"] = result
            if error is not None and not job.get("error"):
                job["error"] = error
        else:
            job["status"] = status
            job["finished_at"] = _now_iso()
            job["finished_at_epoch"] = time.time()
            if result is not None:
                job["result"] = result
            if error is not None:
                job["error"] = error
            elif status == "error" and isinstance(result, dict) and result.get("error"):
                job["error"] = str(result.get("error"))
            _futures.pop(job_id, None)
            snapshot = dict(job)
            _prune_locked()

    if snapshot is not None:
        try:
            from app.services.bots.ml_train_runs import record_ml_train_run_from_job

            record_ml_train_run_from_job(snapshot)
        except Exception:
            pass

    # MEMORY_CENTRIC_REVIEW #31 — hot RAM holds a slim headline; the full
    # payload moves to disk and is hydrated on demand by ``public_ml_job``.
    _offload_terminal_result(job_id)


def is_ml_job_cancelled(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return False
        return job.get("status") == "cancelled" or bool(job.get("cancel_requested"))


def request_ml_job_cancel(job_id: str) -> dict[str, Any]:
    """Request cooperative cancel. Returns {ok, cancelled, status, error?}."""
    from app.services.bots.ml_job_progress import request_ml_cancel_file

    if not job_id:
        return {"ok": False, "cancelled": False, "error": "job_id required"}

    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return {"ok": False, "cancelled": False, "error": "job not found"}
        status = job.get("status")
        if status in _TERMINAL:
            return {
                "ok": True,
                "cancelled": status == "cancelled",
                "status": status,
                "already_finished": True,
            }
        job["cancel_requested"] = True
        progress_path = job.get("progress_path")
        fut = _futures.get(job_id)

    request_ml_cancel_file(progress_path)

    # Queued (not yet running in the pool): try Future.cancel().
    cancelled_future = False
    if fut is not None and not fut.running() and not fut.done():
        cancelled_future = bool(fut.cancel())

    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return {"ok": True, "cancelled": True, "status": "cancelled"}
        if cancelled_future or job.get("status") == "queued":
            # Mark cancelled; persist once via finish_ml_job (single run-history insert).
            job["cancel_requested"] = True
            was_queued = job.get("status") == "queued"
        else:
            was_queued = False

    if cancelled_future or was_queued:
        finish_ml_job(job_id, "cancelled", error="cancelled")
        return {"ok": True, "cancelled": True, "status": "cancelled", "immediate": True}

    with _lock:
        job = _jobs.get(job_id)
        # Running — cooperative; status stays running until worker exits.
        return {
            "ok": True,
            "cancelled": False,
            "status": (job or {}).get("status"),
            "cooperative": True,
        }


def public_ml_job(job: dict[str, Any] | None, *, include_result: bool = True) -> dict[str, Any] | None:
    """API-safe job view (no progress_path / internal fields).

    Strips non-JSON values (``±inf``/NaN, torch modules in ``_wf_bundle``) so
    Starlette ``JSONResponse`` never 500s after a successful train — that
    previously stranded the Lab UI on ``poll_err`` / \"server busy\".
    """
    if not job:
        return None
    out = {
        "job_id": job.get("job_id"),
        "kind": job.get("kind"),
        "strategy": job.get("strategy"),
        "symbol": job.get("symbol"),
        "status": job.get("status"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "created_at": job.get("created_at"),
        "progress": job.get("progress") or {},
        "error": job.get("error"),
        "cancel_requested": bool(job.get("cancel_requested")),
    }
    if include_result and job.get("status") in _TERMINAL:
        result = job.get("result")
        if isinstance(result, dict):
            if result.get("_slimmed"):
                # Hydrate the offloaded payload from disk; fall back to the
                # slim headline if the file is gone (never 500 the poll).
                full = _load_offloaded_result(result)
                if full is not None:
                    out["result"] = _json_safe_ml_value(full)
                else:
                    slim = {
                        k: v for k, v in result.items()
                        if not str(k).startswith("_")
                    }
                    out["result"] = _json_safe_ml_value(slim)
            else:
                # Drop in-memory torch bundles — never belong in HTTP responses.
                safe = {k: v for k, v in result.items() if k != "_wf_bundle"}
                out["result"] = _json_safe_ml_value(safe)
        else:
            out["result"] = result
    return out


def _json_safe_ml_value(value: Any) -> Any:
    """Recursively replace non-finite floats and unknown objects for JSON."""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe_ml_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_ml_value(v) for v in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    # numpy scalars
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe_ml_value(item())
        except Exception:
            return str(value)
    return str(value)


def reset_ml_job_store_for_tests() -> None:
    with _lock:
        _jobs.clear()
        _futures.clear()
