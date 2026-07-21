"""Persistent ML train/validate run history (ML_LAB_IMPROVEMENTS §2.4)."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_connection

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _config_hash(result: dict | None, job: dict | None = None) -> str | None:
    cfg = None
    if isinstance(result, dict):
        cfg = result.get("config")
        if cfg is None and isinstance(result.get("metrics"), dict):
            # Prefer a stable subset if full config absent.
            cfg = {
                k: result["metrics"].get(k)
                for k in ("hidden_dim", "total_timesteps", "n_folds")
                if result["metrics"].get(k) is not None
            } or None
    if cfg is None and isinstance(job, dict):
        cfg = {"kind": job.get("kind"), "strategy": job.get("strategy")}
    if cfg is None:
        return None
    try:
        raw = json.dumps(cfg, sort_keys=True, default=str)
    except Exception:
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _extract_metrics(result: dict | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    metrics: dict[str, Any] = {}
    src = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    for key in (
        "val_accuracy",
        "accuracy",
        "mean_return_pct",
        "best_mean_return",
        "episodes",
        "total_timesteps",
        "train_samples",
        "val_samples",
    ):
        if src.get(key) is not None:
            metrics[key] = src.get(key)
    if result.get("mean_accuracy") is not None:
        metrics["mean_accuracy"] = result.get("mean_accuracy")
    agg = result.get("aggregate") if isinstance(result.get("aggregate"), dict) else {}
    if agg.get("mean_oos_accuracy") is not None:
        metrics["mean_oos_accuracy"] = agg.get("mean_oos_accuracy")
    if result.get("n_folds") is not None:
        metrics["n_folds"] = result.get("n_folds")
    pbo = result.get("pbo")
    if isinstance(pbo, dict) and pbo.get("pbo") is not None:
        metrics["pbo"] = pbo.get("pbo")
    elif pbo is not None and not isinstance(pbo, dict):
        metrics["pbo"] = pbo
    return metrics


def _extract_timeframe(result: dict | None, job: dict | None = None) -> str | None:
    """Best-effort timeframe from train/validate result or job payload."""
    try:
        from app.services.bots.ml_model_artifacts import normalize_model_timeframe
    except Exception:
        def normalize_model_timeframe(tf):  # type: ignore
            return (tf or "1m").strip() or "1m"

    candidates = []
    if isinstance(result, dict):
        candidates.append(result.get("timeframe"))
        cfg = result.get("config") if isinstance(result.get("config"), dict) else {}
        candidates.append(cfg.get("timeframe"))
        tw = result.get("training_window") if isinstance(result.get("training_window"), dict) else {}
        candidates.append(tw.get("timeframe"))
        vp = result.get("validation_persisted") if isinstance(result.get("validation_persisted"), dict) else {}
        candidates.append(vp.get("timeframe"))
    if isinstance(job, dict):
        candidates.append(job.get("timeframe"))
        jcfg = job.get("config") if isinstance(job.get("config"), dict) else {}
        candidates.append(jcfg.get("timeframe"))
    for raw in candidates:
        if raw:
            return normalize_model_timeframe(str(raw))
    return None


def record_ml_train_run_from_job(job: dict[str, Any] | None) -> str | None:
    """Insert one row from a finished in-memory job. Best-effort; never raises."""
    if not isinstance(job, dict):
        return None
    status = job.get("status")
    if status not in ("done", "error", "cancelled"):
        return None

    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    ok = 1 if status == "done" and result.get("ok") is not False and status != "cancelled" else 0
    if status == "cancelled":
        ok = 0

    started = job.get("started_at") or job.get("created_at")
    finished = job.get("finished_at") or _now_iso()
    t0 = _parse_iso_epoch(started)
    t1 = _parse_iso_epoch(finished) or (job.get("finished_at_epoch") if isinstance(job.get("finished_at_epoch"), (int, float)) else None)
    duration_ms = None
    if t0 is not None and t1 is not None and t1 >= t0:
        duration_ms = int((t1 - t0) * 1000)

    error = job.get("error") or (result.get("error") if result else None)
    if status == "cancelled":
        error = error or "cancelled"

    version_id = None
    if result:
        version_id = result.get("version_id") or (result.get("metadata") or {}).get("version_id")
        if not version_id:
            version_id = result.get("trained_at") or (result.get("metadata") or {}).get("trained_at")

    metrics = _extract_metrics(result)
    timeframe = _extract_timeframe(result, job)
    run_id = str(uuid.uuid4())
    try:
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO ml_train_runs (
                    id, kind, strategy, symbol, started_at, finished_at,
                    duration_ms, ok, error, metrics_json, config_hash,
                    version_id, job_id, created_at, timeframe
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(job.get("kind") or "train"),
                    str(job.get("strategy") or "").upper(),
                    str(job.get("symbol") or "").upper(),
                    started,
                    finished,
                    duration_ms,
                    ok,
                    str(error) if error else None,
                    json.dumps(metrics) if metrics else None,
                    _config_hash(result, job),
                    str(version_id) if version_id else None,
                    job.get("job_id"),
                    _now_iso(),
                    timeframe,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return run_id
    except Exception:
        logger.exception("Failed to persist ml_train_run for job %s", job.get("job_id"))
        return None


def list_ml_train_runs(
    *,
    symbol: str | None = None,
    strategy: str | None = None,
    timeframe: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 20), 100))
    clauses: list[str] = []
    params: list[Any] = []
    if symbol:
        clauses.append("symbol = ?")
        params.append(str(symbol).upper())
    if strategy:
        clauses.append("strategy = ?")
        params.append(str(strategy).upper())
    if timeframe:
        try:
            from app.services.bots.ml_model_artifacts import normalize_model_timeframe
            tf = normalize_model_timeframe(timeframe)
        except Exception:
            tf = str(timeframe).strip() or "1m"
        # Include legacy rows with NULL timeframe so history is not empty after upgrade.
        clauses.append("(timeframe = ? OR timeframe IS NULL OR timeframe = '')")
        params.append(tf)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT id, kind, strategy, symbol, started_at, finished_at,
                   duration_ms, ok, error, metrics_json, config_hash,
                   version_id, job_id, created_at, timeframe
            FROM ml_train_runs
            {where}
            ORDER BY finished_at DESC
            LIMIT ?
            """,
            params,
        )
        rows = cursor.fetchall()
        return [_row_to_run(row) for row in rows]
    finally:
        conn.close()


def _parse_json(raw, default=None):
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _row_to_run(row) -> dict[str, Any]:
    if isinstance(row, dict):
        item = dict(row)
    else:
        # Prefer sqlite3.Row / mapping when available.
        try:
            keys = row.keys()  # type: ignore[attr-defined]
            item = {k: row[k] for k in keys}
        except Exception:
            item = {
                "id": row[0],
                "kind": row[1],
                "strategy": row[2],
                "symbol": row[3],
                "started_at": row[4],
                "finished_at": row[5],
                "duration_ms": row[6],
                "ok": row[7],
                "error": row[8],
                "metrics_json": row[9],
                "config_hash": row[10],
                "version_id": row[11],
                "job_id": row[12],
                "created_at": row[13],
                "timeframe": row[14] if len(row) > 14 else None,
            }
    metrics = _parse_json(item.pop("metrics_json", None), {})
    return {
        "id": item.get("id"),
        "kind": item.get("kind"),
        "strategy": item.get("strategy"),
        "symbol": item.get("symbol"),
        "timeframe": item.get("timeframe"),
        "started_at": item.get("started_at"),
        "finished_at": item.get("finished_at"),
        "duration_ms": item.get("duration_ms"),
        "ok": bool(item.get("ok")),
        "error": item.get("error"),
        "metrics": metrics or {},
        "config_hash": item.get("config_hash"),
        "version_id": item.get("version_id"),
        "job_id": item.get("job_id"),
        "created_at": item.get("created_at"),
    }
