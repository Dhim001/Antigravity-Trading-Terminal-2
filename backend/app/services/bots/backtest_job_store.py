"""Persistent backtest job queue — survives server restarts."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_connection

_STATUSES = frozenset({"pending", "running", "completed", "failed", "cancelled"})


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
                json.dumps({"pct": 0, "phase": "queued", "message": "Queued…"}),
                client_key,
                _now_iso(),
                _now_iso() if status == "running" else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return job_id


def update_job_progress(job_id: str, progress: dict) -> None:
    if not job_id:
        return
    payload = {**(progress or {}), "job_id": job_id}
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
    """Mark interrupted running jobs as pending for worker resume."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE backtest_jobs
            SET status = 'pending', started_at = NULL,
                progress_json = ?
            WHERE status = 'running'
            """,
            (json.dumps({"pct": 0, "phase": "recover", "message": "Resuming after restart…"}),),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def claim_next_pending_job() -> dict[str, Any] | None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, request_json, progress_json, client_key, created_at
            FROM backtest_jobs
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT 1
            """,
        )
        row = cursor.fetchone()
        if not row:
            return None
        item = _row_to_job(row)
        cursor.execute(
            """
            UPDATE backtest_jobs
            SET status = 'running', started_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (_now_iso(), item["id"]),
        )
        if cursor.rowcount == 0:
            conn.commit()
            return None
        conn.commit()
        item["status"] = "running"
        return item
    finally:
        conn.close()


def get_backtest_job(job_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, status, request_json, progress_json, run_id, error,
                   results_json, client_key, created_at, started_at, finished_at
            FROM backtest_jobs WHERE id = ?
            """,
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
            """
            SELECT id, status, request_json, progress_json, run_id, error,
                   results_json, client_key, created_at, started_at, finished_at
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
    limit = max(1, min(limit, 100))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if status:
            cursor.execute(
                """
                SELECT id, status, request_json, progress_json, run_id, error,
                       results_json, client_key, created_at, started_at, finished_at
                FROM backtest_jobs WHERE status = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (status, limit),
            )
        else:
            cursor.execute(
                """
                SELECT id, status, request_json, progress_json, run_id, error,
                       results_json, client_key, created_at, started_at, finished_at
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
        }
    item["request"] = _parse_json_field(item.pop("request_json", None), {})
    item["progress"] = _parse_json_field(item.pop("progress_json", None), {})
    item["results"] = _parse_json_field(item.pop("results_json", None), None)
    return item
