"""Persistent ML train/validate run history (ML_LAB_IMPROVEMENTS §2.4)."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
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
        "fit_samples",
        "epochs_trained",
        "epochs_budget",
        "val_loss",
        "train_accuracy",
        "best_score",
        "best_trial",
        "trials_completed",
    ):
        if src.get(key) is not None:
            metrics[key] = _finite_or_none(src.get(key))
    # Sweep / Optuna summaries sometimes nest best metrics.
    best = result.get("best") if isinstance(result.get("best"), dict) else {}
    if best.get("mean_return_pct") is not None:
        metrics.setdefault("best_mean_return", _finite_or_none(best.get("mean_return_pct")))
    if best.get("score") is not None:
        metrics.setdefault("best_score", _finite_or_none(best.get("score")))
    if src.get("early_stopped") is not None:
        metrics["early_stopped"] = bool(src.get("early_stopped"))
    if result.get("early_stopped") is not None:
        metrics.setdefault("early_stopped", bool(result.get("early_stopped")))
    if result.get("epochs_trained") is not None:
        metrics.setdefault("epochs_trained", _finite_or_none(result.get("epochs_trained")))
    if result.get("mean_accuracy") is not None:
        metrics["mean_accuracy"] = _finite_or_none(result.get("mean_accuracy"))
    agg = result.get("aggregate") if isinstance(result.get("aggregate"), dict) else {}
    if agg.get("mean_oos_accuracy") is not None:
        metrics["mean_oos_accuracy"] = _finite_or_none(agg.get("mean_oos_accuracy"))
    if result.get("n_folds") is not None:
        metrics["n_folds"] = result.get("n_folds")
    pbo = result.get("pbo")
    if isinstance(pbo, dict) and pbo.get("pbo") is not None:
        metrics["pbo"] = _finite_or_none(pbo.get("pbo"))
    elif pbo is not None and not isinstance(pbo, dict):
        metrics["pbo"] = _finite_or_none(pbo)
    # Drop keys emptied solely by non-finite scrubbing.
    return {k: v for k, v in metrics.items() if v is not None}


def _finite_or_none(value: Any) -> Any:
    """JSON-safe scalar — Starlette rejects ±inf/NaN."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        try:
            f = float(value)
        except (TypeError, ValueError):
            return value
        if math.isnan(f) or math.isinf(f):
            return None
        return value
    return value


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


def _extract_version_id(result: dict | None, job: dict | None = None) -> str | None:
    """Best-effort model version / pin from train or validate payloads."""
    result = result if isinstance(result, dict) else {}
    job = job if isinstance(job, dict) else {}
    meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    vp = result.get("validation_persisted") if isinstance(result.get("validation_persisted"), dict) else {}
    cfg = result.get("config") if isinstance(result.get("config"), dict) else {}
    jcfg = job.get("config") if isinstance(job.get("config"), dict) else {}
    candidates = [
        result.get("version_id"),
        meta.get("version_id"),
        vp.get("version_id"),
        result.get("model_version"),
        meta.get("model_version"),
        cfg.get("model_version"),
        job.get("model_version"),
        jcfg.get("model_version"),
        result.get("trained_at"),
        meta.get("trained_at"),
        vp.get("trained_at"),
        job.get("trained_at"),
    ]
    for raw in candidates:
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return None


def _extract_display_name(result: dict | None) -> str | None:
    if not isinstance(result, dict):
        return None
    meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    for raw in (
        result.get("display_name"),
        meta.get("display_name"),
        (result.get("validation_persisted") or {}).get("display_name")
        if isinstance(result.get("validation_persisted"), dict)
        else None,
    ):
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def _model_identity(strategy: str | None) -> dict[str, str | None]:
    """Human-readable model family + primary artifact for a strategy."""
    key = str(strategy or "").upper()
    try:
        from app.services.bots.ml_registry import model_type_label, primary_artifact_name

        return {
            "model_label": model_type_label(key) if key else None,
            "artifact": primary_artifact_name(key) if key else None,
        }
    except Exception:
        return {"model_label": key.lower() if key else None, "artifact": None}


def _lookup_version_meta(
    strategy: str | None,
    symbol: str | None,
    timeframe: str | None,
    version_id: str | None = None,
    *,
    versions_cache: dict[tuple[str, str, str], list] | None = None,
) -> dict[str, Any]:
    """Resolve display_name for a known version_id from the on-disk index.

    Never invents a champion pin when ``version_id`` is missing or unmatched —
    that would rewrite historical validate/sweep rows to today's current model.
    """
    out: dict[str, Any] = {}
    if not strategy or not symbol or not version_id:
        return out
    try:
        from app.services.bots.ml_model_artifacts import (
            list_model_versions,
            model_root_for,
            normalize_model_timeframe,
            version_id_from_iso,
        )
    except Exception:
        return out
    try:
        tf = normalize_model_timeframe(timeframe) if timeframe else "1m"
        cache_key = (str(strategy).upper(), str(symbol).upper(), tf)
        if versions_cache is not None and cache_key in versions_cache:
            versions = versions_cache[cache_key]
        else:
            root = model_root_for(str(strategy), str(symbol), tf)
            versions = list_model_versions(root)
            if versions_cache is not None:
                versions_cache[cache_key] = versions
    except Exception:
        return out
    if not versions:
        return out

    needle = str(version_id).strip()
    vid = version_id_from_iso(needle)
    match = None
    for entry in versions:
        eid = str(entry.get("version_id") or "")
        eat = str(entry.get("trained_at") or "")
        if eid == needle or eid == vid or eat == needle:
            match = entry
            break
    if not isinstance(match, dict):
        return out
    if match.get("version_id"):
        out["version_id"] = str(match["version_id"])
    if match.get("display_name"):
        out["display_name"] = str(match["display_name"]).strip()[:80]
    if match.get("trained_at"):
        out["trained_at"] = str(match["trained_at"])
    return out


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

    version_id = _extract_version_id(result, job)
    metrics = _extract_metrics(result)
    display_name = _extract_display_name(result)
    timeframe = _extract_timeframe(result, job)
    # Only attach a custom name when we already have a real pin — never invent one.
    if version_id and not display_name:
        looked = _lookup_version_meta(
            job.get("strategy"),
            job.get("symbol"),
            timeframe or job.get("timeframe"),
            version_id,
        )
        if looked.get("display_name"):
            display_name = looked["display_name"]
        if looked.get("version_id"):
            version_id = looked["version_id"]
    if display_name:
        metrics = {**metrics, "display_name": display_name}
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
    batch_id: str | None = None,
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
    if batch_id:
        # Batch items span strategies — filter purely via the item job ids.
        try:
            from app.database import ensure_ml_batch_tables

            ensure_ml_batch_tables()
        except Exception:
            logger.debug("ml_batch table ensure failed for runs filter", exc_info=True)
        clauses.append(
            "job_id IN (SELECT job_id FROM ml_batch_items "
            "WHERE batch_id = ? AND job_id IS NOT NULL)"
        )
        params.append(str(batch_id))
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
        runs = [_row_to_run(row) for row in rows]
        # Only resolve custom names for rows that already have a pin — never invent pins.
        versions_cache: dict[tuple[str, str, str], list] = {}
        for run in runs:
            if run.get("display_name") or not run.get("version_id"):
                continue
            looked = _lookup_version_meta(
                run.get("strategy"),
                run.get("symbol"),
                run.get("timeframe"),
                run.get("version_id"),
                versions_cache=versions_cache,
            )
            if looked.get("display_name"):
                run["display_name"] = looked["display_name"]
            if looked.get("version_id"):
                run["version_id"] = looked["version_id"]
        return runs
    finally:
        conn.close()


def prune_ml_train_runs(retention_days: int) -> int:
    """Delete ``ml_train_runs`` rows older than ``retention_days`` (#30).

    Same pattern as ``prune_optimization_runs`` / ``prune_backtest_jobs`` —
    wired into the startup retention pass in ``server.py``. Returns rows
    deleted (never raises).
    """
    if retention_days <= 0:
        return 0
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=int(retention_days))
    ).isoformat().replace("+00:00", "Z")
    try:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM ml_train_runs WHERE created_at < ?", (cutoff,))
            conn.commit()
            return cursor.rowcount or 0
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to prune ml_train_runs")
        return 0


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
    if isinstance(metrics, dict):
        metrics = {k: _finite_or_none(v) for k, v in metrics.items()}
        metrics = {k: v for k, v in metrics.items() if v is not None}
    else:
        metrics = {}
    strategy = item.get("strategy")
    identity = _model_identity(strategy)
    display_name = None
    if isinstance(metrics, dict) and metrics.get("display_name"):
        display_name = str(metrics.get("display_name"))
        metrics = {k: v for k, v in metrics.items() if k != "display_name"}
    # model_name = stable family label; custom nicknames live in display_name only.
    model_name = identity.get("model_label") or (
        str(strategy).lower() if strategy else None
    )
    return {
        "id": item.get("id"),
        "kind": item.get("kind"),
        "strategy": strategy,
        "symbol": item.get("symbol"),
        "timeframe": item.get("timeframe"),
        "started_at": item.get("started_at"),
        "finished_at": item.get("finished_at"),
        "duration_ms": item.get("duration_ms"),
        "ok": bool(item.get("ok")),
        "error": item.get("error"),
        "metrics": metrics,
        "config_hash": item.get("config_hash"),
        "version_id": item.get("version_id"),
        "job_id": item.get("job_id"),
        "created_at": item.get("created_at"),
        "model_label": identity.get("model_label"),
        "artifact": identity.get("artifact"),
        "display_name": display_name,
        "model_name": model_name,
    }
