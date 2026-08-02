"""Persistent backtest job queue — survives server restarts."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from app.db.connection import get_connection
from app.services.bots.backtest_checkpoint import is_resumable_job

_STATUSES = frozenset({"pending", "running", "completed", "failed", "cancelled"})

_JOB_SELECT_COLS = (
    "id, status, request_json, progress_json, run_id, error, "
    "results_json, client_key, created_at, started_at, finished_at, checkpoint_json"
)
_JOB_SELECT_COLS_NO_RESULTS = (
    "id, status, request_json, progress_json, run_id, error, "
    "NULL AS results_json, client_key, created_at, started_at, finished_at, checkpoint_json"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def create_backtest_job(request: dict, *, status: str = "running", client_key: str | None = None) -> str:
    job_id = str(uuid.uuid4())
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO backtest_jobs (
                id, status, request_json, progress_json, client_key, created_at, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                status if status in _STATUSES else "pending",
                json.dumps(request or {}),
                json.dumps({
                    "pct": 0,
                    "phase": "queued",
                    "message": "Queued…",
                    "updated_at": _now_iso(),
                }),
                client_key,
                _now_iso(),
                _now_iso() if status == "running" else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return job_id


def start_job_execution(job_id: str) -> None:
    """Mark a pending job as running when a deferred task begins."""
    if not job_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE backtest_jobs
            SET status = 'running', started_at = COALESCE(started_at, ?),
                finished_at = NULL, error = NULL
            WHERE id = ? AND status IN ('pending', 'running')
            """,
            (_now_iso(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_job_progress(job_id: str, progress: dict) -> None:
    if not job_id:
        return
    # Always stamp updated_at so the FE stall detector can trust server freshness
    # even when pct/bar are unchanged (heartbeat / long precompute).
    payload = {**(progress or {}), "job_id": job_id, "updated_at": _now_iso()}
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE backtest_jobs SET progress_json = ? WHERE id = ?",
            (json.dumps(payload), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def save_job_checkpoint(job_id: str, checkpoint: dict | None) -> None:
    """Persist discrete sweep/WF progress without touching results_json."""
    if not job_id:
        return
    payload = dict(checkpoint or {})
    payload.setdefault("updated_at", _now_iso())
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE backtest_jobs SET checkpoint_json = ? WHERE id = ?",
            (json.dumps(payload), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def load_job_checkpoint(job_id: str) -> dict[str, Any] | None:
    if not job_id:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT checkpoint_json FROM backtest_jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            return None
        raw = row["checkpoint_json"] if isinstance(row, dict) else row[0]
        return _parse_json_field(raw, None)
    finally:
        conn.close()


def clear_job_checkpoint(job_id: str) -> None:
    if not job_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE backtest_jobs SET checkpoint_json = NULL WHERE id = ?",
            (job_id,),
        )
        conn.commit()
    finally:
        conn.close()


def resume_backtest_job(job_id: str) -> dict[str, Any]:
    """Re-queue a failed/cancelled/pending job without wiping checkpoint_json.

    Returns ``{ok, job?, error?}``.
    """
    if not job_id:
        return {"ok": False, "error": "job_id required"}
    job = get_backtest_job(job_id, include_results=False)
    if not job:
        return {"ok": False, "error": "Job not found"}
    if job.get("status") == "running":
        return {"ok": False, "error": "Job is already running", "job": job}
    if job.get("status") == "completed":
        return {"ok": False, "error": "Job already completed", "job": job}
    if not is_resumable_job(job) and job.get("status") not in ("pending", "failed", "cancelled"):
        return {"ok": False, "error": "Job is not resumable", "job": job}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE backtest_jobs
            SET status = 'pending',
                started_at = NULL,
                finished_at = NULL,
                error = NULL,
                progress_json = ?
            WHERE id = ? AND status IN ('pending', 'failed', 'cancelled')
            """,
            (
                json.dumps({
                    "pct": 0,
                    "phase": "recover",
                    "message": "Resume queued…",
                    "updated_at": _now_iso(),
                    "job_id": job_id,
                }),
                job_id,
            ),
        )
        if cursor.rowcount == 0:
            conn.commit()
            return {"ok": False, "error": "Could not re-queue job", "job": job}
        conn.commit()
    finally:
        conn.close()
    refreshed = get_backtest_job(job_id, include_results=False)
    return {"ok": True, "job": refreshed}


def set_job_status(
    job_id: str,
    status: str,
    *,
    run_id: str | None = None,
    error: str | None = None,
    results: dict | None = None,
) -> None:
    if not job_id or status not in _STATUSES:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Clear checkpoint on successful completion; keep on failure for resume.
        if status == "completed":
            cursor.execute(
                """
                UPDATE backtest_jobs
                SET status = ?, run_id = COALESCE(?, run_id), error = ?,
                    results_json = COALESCE(?, results_json), finished_at = ?,
                    checkpoint_json = NULL
                WHERE id = ?
                """,
                (
                    status,
                    run_id,
                    error,
                    json.dumps(results) if results is not None else None,
                    _now_iso(),
                    job_id,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE backtest_jobs
                SET status = ?, run_id = COALESCE(?, run_id), error = ?,
                    results_json = COALESCE(?, results_json), finished_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    run_id,
                    error,
                    json.dumps(results) if results is not None else None,
                    _now_iso(),
                    job_id,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def is_job_cancelled(job_id: str) -> bool:
    if not job_id:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT status FROM backtest_jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            return False
        status = row["status"] if isinstance(row, dict) else row[0]
        return status == "cancelled"
    finally:
        conn.close()


def request_cancel_job(job_id: str) -> bool:
    if not job_id:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE backtest_jobs
            SET status = 'cancelled', finished_at = ?
            WHERE id = ? AND status IN ('pending', 'running')
            """,
            (_now_iso(), job_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def recover_stale_running_jobs() -> int:
    """Mark interrupted running jobs as pending for worker resume.

    Preserves ``checkpoint_json`` so sweep/WF progress is not lost.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT id, progress_json, checkpoint_json FROM backtest_jobs
            WHERE status = 'running'
            """,
        )
        rows = cursor.fetchall() or []
        if not rows:
            return 0
        count = 0
        for row in rows:
            job_id = row["id"] if isinstance(row, dict) else row[0]
            progress_raw = row["progress_json"] if isinstance(row, dict) else row[1]
            progress = _parse_json_field(progress_raw, {}) or {}
            pct = int(progress.get("pct") or 0) if isinstance(progress, dict) else 0
            new_progress = {
                **(progress if isinstance(progress, dict) else {}),
                "pct": max(0, pct),
                "phase": "recover",
                "message": "Resuming after restart…",
                "updated_at": _now_iso(),
            }
            new_progress.pop("worker_pid", None)
            cursor.execute(
                """
                UPDATE backtest_jobs
                SET status = 'pending', started_at = NULL, progress_json = ?
                WHERE id = ? AND status = 'running'
                """,
                (json.dumps(new_progress), job_id),
            )
            if cursor.rowcount:
                count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import os

        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def recover_dead_worker_jobs() -> int:
    """Re-queue running jobs whose ``worker_pid`` heartbeat process is gone.

    Leaves jobs without a worker_pid alone (legacy / in-process create_task).
    Preserves checkpoint_json.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, progress_json FROM backtest_jobs
            WHERE status = 'running'
            """,
        )
        rows = cursor.fetchall() or []
        count = 0
        for row in rows:
            job_id = row["id"] if isinstance(row, dict) else row[0]
            progress = _parse_json_field(
                row["progress_json"] if isinstance(row, dict) else row[1],
                {},
            ) or {}
            raw_pid = progress.get("worker_pid") if isinstance(progress, dict) else None
            try:
                pid = int(raw_pid)
            except (TypeError, ValueError):
                continue
            if _pid_is_alive(pid):
                continue
            new_progress = {
                **progress,
                "phase": "recover",
                "message": f"Worker pid={pid} died — re-queued with checkpoint…",
                "updated_at": _now_iso(),
            }
            new_progress.pop("worker_pid", None)
            cursor.execute(
                """
                UPDATE backtest_jobs
                SET status = 'pending', started_at = NULL, progress_json = ?
                WHERE id = ? AND status = 'running'
                """,
                (json.dumps(new_progress), job_id),
            )
            if cursor.rowcount:
                count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def fail_stale_pending_jobs(*, max_age_hours: float = 6.0, recover_age_hours: float = 1.0) -> int:
    """Fail pending jobs that never started (or sat in recover) for too long.

    Jobs with a durable checkpoint are excluded from the short recover-age path
    so they remain claimable by the worker after a restart.
    """
    max_age_hours = max(0.25, float(max_age_hours or 6.0))
    recover_age_hours = max(0.1, float(recover_age_hours or 1.0))
    now_ts = datetime.now(timezone.utc).timestamp()
    cutoff = now_ts - (max_age_hours * 3600.0)
    recover_cutoff = now_ts - (recover_age_hours * 3600.0)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, created_at, progress_json, checkpoint_json FROM backtest_jobs
            WHERE status = 'pending'
            """,
        )
        rows = cursor.fetchall() or []
        stale_ids: list[str] = []
        for row in rows:
            job_id = row["id"] if isinstance(row, dict) else row[0]
            created = row["created_at"] if isinstance(row, dict) else row[1]
            progress_raw = row["progress_json"] if isinstance(row, dict) else row[2]
            checkpoint_raw = row["checkpoint_json"] if isinstance(row, dict) else row[3]
            try:
                created_ts = datetime.fromisoformat(
                    str(created).replace("Z", "+00:00"),
                ).timestamp()
            except Exception:
                continue
            has_checkpoint = bool(_parse_json_field(checkpoint_raw, None))
            phase = ""
            try:
                progress = json.loads(progress_raw) if isinstance(progress_raw, str) else (progress_raw or {})
                phase = str((progress or {}).get("phase") or "")
            except Exception:
                phase = ""
            if created_ts <= cutoff:
                stale_ids.append(str(job_id))
            elif phase == "recover" and created_ts <= recover_cutoff and not has_checkpoint:
                stale_ids.append(str(job_id))
        if not stale_ids:
            return 0
        err = (
            f"Abandoned pending job older than {max_age_hours:g}h "
            "(never started after queue/restart)"
        )
        now = _now_iso()
        for job_id in stale_ids:
            cursor.execute(
                """
                UPDATE backtest_jobs
                SET status = 'failed', error = ?, finished_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (err, now, job_id),
            )
        conn.commit()
        return len(stale_ids)
    finally:
        conn.close()


def claim_next_pending_job(
    *,
    accept: Callable[[dict[str, Any]], bool] | None = None,
    peek_limit: int = 32,
) -> dict[str, Any] | None:
    """Atomically claim the oldest pending job that passes ``accept``.

    ``accept`` avoids claim/reject races between the API in-process worker and
    the heavy-job sidecar (heavy vs light filtering).
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        limit = max(1, min(int(peek_limit or 32), 100))
        cursor.execute(
            f"""
            SELECT {_JOB_SELECT_COLS}
            FROM backtest_jobs
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall() or []
        for row in rows:
            item = _row_to_job(row)
            if accept is not None and not accept(item):
                continue
            cursor.execute(
                """
                UPDATE backtest_jobs
                SET status = 'running', started_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (_now_iso(), item["id"]),
            )
            if cursor.rowcount == 0:
                continue
            conn.commit()
            item["status"] = "running"
            return item
        conn.commit()
        return None
    finally:
        conn.close()


def get_backtest_job(job_id: str, *, include_results: bool = True) -> dict[str, Any] | None:
    """Load a job. Polling should use ``include_results=False`` — results payloads
    can be multi‑MB and blow the FE's default 8s HTTP timeout, causing a false stall.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cols = _JOB_SELECT_COLS if include_results else _JOB_SELECT_COLS_NO_RESULTS
        cursor.execute(
            f"SELECT {cols} FROM backtest_jobs WHERE id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        return _row_to_job(row) if row else None
    finally:
        conn.close()


def get_active_backtest_job() -> dict[str, Any] | None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT {_JOB_SELECT_COLS_NO_RESULTS}
            FROM backtest_jobs
            WHERE status IN ('pending', 'running')
            ORDER BY created_at DESC
            LIMIT 1
            """,
        )
        row = cursor.fetchone()
        return _row_to_job(row) if row else None
    finally:
        conn.close()


def list_backtest_jobs(*, limit: int = 20, status: str | None = None) -> list[dict[str, Any]]:
    """List recent jobs without materializing results_json (Jobs tab / history)."""
    limit = max(1, min(limit, 100))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if status:
            cursor.execute(
                f"""
                SELECT {_JOB_SELECT_COLS_NO_RESULTS}
                FROM backtest_jobs WHERE status = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (status, limit),
            )
        else:
            cursor.execute(
                f"""
                SELECT {_JOB_SELECT_COLS_NO_RESULTS}
                FROM backtest_jobs
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            )
        return [_row_to_job(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def _parse_json_field(raw, default=None):
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _row_to_job(row) -> dict[str, Any]:
    if isinstance(row, dict):
        item = dict(row)
    else:
        # Positional layout must match _JOB_SELECT_COLS / NO_RESULTS.
        item = {
            "id": row[0],
            "status": row[1],
            "request_json": row[2],
            "progress_json": row[3],
            "run_id": row[4],
            "error": row[5],
            "results_json": row[6],
            "client_key": row[7],
            "created_at": row[8],
            "started_at": row[9],
            "finished_at": row[10],
            "checkpoint_json": row[11] if len(row) > 11 else None,
        }
    item["request"] = _parse_json_field(item.pop("request_json", None), {})
    item["progress"] = _parse_json_field(item.pop("progress_json", None), {})
    item["results"] = _parse_json_field(item.pop("results_json", None), None)
    item["checkpoint"] = _parse_json_field(item.pop("checkpoint_json", None), None)
    item["resumable"] = is_resumable_job(item)
    return item


def prune_backtest_jobs(retention_days: int) -> int:
    """Delete finished backtest jobs older than retention_days."""
    if retention_days <= 0:
        return 0
    from datetime import timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(retention_days))).isoformat().replace("+00:00", "Z")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            DELETE FROM backtest_jobs
            WHERE status IN ('completed', 'failed', 'cancelled')
              AND finished_at IS NOT NULL
              AND finished_at < ?
            """,
            (cutoff,),
        )
        deleted = cursor.rowcount or 0
        conn.commit()
        return deleted
    finally:
        conn.close()
