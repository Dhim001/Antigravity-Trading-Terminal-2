"""Durable ML full-pipeline orchestrator (research-standard close).

Persists one ``pipeline_id`` in SQLite and drives Search → Train → Validate →
Holdout backtest → Gate → Deploy. The Lab starts / cancels / approves and
mirrors this row; it does not own stage advance.

Modeled on ``ml_batch_runner``: injectable stage executor for tests, crash
recovery, cooperative cancel, retry from the failed stage.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

PIPELINE_PROFILES = frozenset({"research", "retrain"})
AUTO_DEPLOY_MODES = frozenset({"paper", "approval", "full_auto"})

# Wire stages (plus terminals). SEARCH is skipped for profile=retrain.
PIPELINE_STAGES = (
    "SEARCH",
    "TRAINING",
    "VALIDATING",
    "BACKTESTING",
    "GATE_CHECK",
    "READY_TO_DEPLOY",
    "DEPLOYED",
    "GATE_FAILED",
    "ERROR",
)

PIPELINE_STATUSES = frozenset({
    "queued",
    "running",
    "waiting_approval",
    "done",
    "failed",
    "cancelled",
    "gate_failed",
})
_TERMINAL = frozenset({"done", "failed", "cancelled", "gate_failed"})
_ACTIVE = frozenset({"queued", "running", "waiting_approval"})

_FLOW_RESEARCH = (
    "SEARCH",
    "TRAINING",
    "VALIDATING",
    "BACKTESTING",
    "GATE_CHECK",
    "READY_TO_DEPLOY",
    "DEPLOYED",
)
_FLOW_RETRAIN = (
    "TRAINING",
    "VALIDATING",
    "BACKTESTING",
    "GATE_CHECK",
    "READY_TO_DEPLOY",
    "DEPLOYED",
)

_runner_lock = threading.Lock()
_runner_tasks: dict[str, asyncio.Task] = {}
_tables_ready = False
_resumed = False

StageExecutor = Callable[[dict, str], Awaitable[dict]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_to_ms(iso: Any) -> int | None:
    if iso is None:
        return None
    if isinstance(iso, (int, float)):
        v = float(iso)
        return int(v if v > 1e12 else v * 1000)
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def _conn():
    from app.db.connection import get_connection

    return get_connection()


def _ensure_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    with _runner_lock:
        if _tables_ready:
            return
        try:
            from app.database import ensure_ml_pipeline_tables

            ensure_ml_pipeline_tables()
        except Exception:
            logger.debug("ml_pipeline table ensure failed", exc_info=True)
        _tables_ready = True


def reset_ml_pipeline_runner_for_tests() -> None:
    global _tables_ready, _resumed
    with _runner_lock:
        _runner_tasks.clear()
        _tables_ready = False
        _resumed = False


def normalize_profile(profile: Any) -> str:
    p = str(profile or "research").strip().lower()
    return p if p in PIPELINE_PROFILES else "research"


def normalize_auto_deploy_mode(mode: Any) -> str:
    m = str(mode or "paper").strip().lower()
    return m if m in AUTO_DEPLOY_MODES else "paper"


def first_stage(profile: str, *, stop_after_validate: bool = False) -> str:
    flow = stage_flow(profile, stop_after_validate=stop_after_validate)
    return flow[0] if flow else "TRAINING"


def stage_flow(profile: str, *, stop_after_validate: bool = False) -> tuple[str, ...]:
    base = _FLOW_RETRAIN if normalize_profile(profile) == "retrain" else _FLOW_RESEARCH
    if stop_after_validate:
        out = []
        for stage in base:
            out.append(stage)
            if stage == "VALIDATING":
                break
        return tuple(out)
    return base


def next_stage(
    profile: str,
    current: str,
    *,
    stop_after_validate: bool = False,
) -> str | None:
    flow = stage_flow(profile, stop_after_validate=stop_after_validate)
    try:
        idx = flow.index(current)
    except ValueError:
        return None
    if idx + 1 >= len(flow):
        return None
    return flow[idx + 1]


def is_paper_execution(state: Any = None, execution_mode: Any = None) -> bool:
    """Mirror frontend ``isPaperExecutionMode`` for server-side auto-deploy."""
    em = str(execution_mode or "").strip().lower()
    if em in ("paper", "simulated"):
        return True
    if em == "broker":
        return False
    try:
        from app.config import TERMINAL_MODE

        mode = str(getattr(state, "terminal_mode", None) or TERMINAL_MODE or "")
    except Exception:
        mode = ""
    return mode in ("SIMULATED", "LIVE_MASSIVE")


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------


def _load_json(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _dump_json(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, default=str)
    except Exception:
        return None


def _run_from_row(row: dict) -> dict[str, Any]:
    cfg = _load_json(row.get("config_json"))
    elapsed = _load_json(row.get("stage_elapsed_json"))
    return {
        "pipeline_id": row.get("id"),
        "profile": normalize_profile(row.get("profile")),
        "stage": row.get("stage") or "SEARCH",
        "status": row.get("status") or "queued",
        "strategy": row.get("strategy"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "config": cfg if isinstance(cfg, dict) else {},
        "auto_deploy_mode": normalize_auto_deploy_mode(row.get("auto_deploy_mode")),
        "stop_after_validate": bool(row.get("stop_after_validate")),
        "cancel_requested": bool(row.get("cancel_requested")),
        "pending_approval": bool(row.get("pending_approval")),
        "sweep_job_id": row.get("sweep_job_id"),
        "train_job_id": row.get("train_job_id"),
        "validate_job_id": row.get("validate_job_id"),
        "backtest_job_id": row.get("backtest_job_id"),
        "bot_id": row.get("bot_id"),
        "search_result": _load_json(row.get("search_json")),
        "train_result": _load_json(row.get("train_json")),
        "validation_result": _load_json(row.get("validation_json")),
        "backtest_result": _load_json(row.get("backtest_json")),
        "gate_result": _load_json(row.get("gate_json")),
        "last_error": row.get("last_error"),
        "stage_elapsed": elapsed if isinstance(elapsed, dict) else {},
        "started_at": row.get("started_at"),
        "stage_started_at": row.get("stage_started_at"),
        "completed_at": row.get("completed_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "owned_by_server": True,
    }


def _event_from_row(row: dict) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "pipeline_id": row.get("pipeline_id"),
        "from": row.get("from_stage"),
        "to": row.get("to_stage"),
        "elapsed_ms": row.get("elapsed_ms"),
        "error": row.get("error"),
        "job_id": row.get("job_id"),
        "created_at": row.get("created_at"),
        "timestamp": _iso_to_ms(row.get("created_at")),
    }


def list_pipeline_events(pipeline_id: str, *, limit: int = 80) -> list[dict[str, Any]]:
    _ensure_tables()
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, pipeline_id, from_stage, to_stage, elapsed_ms, error, job_id, created_at
            FROM ml_pipeline_events
            WHERE pipeline_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (pipeline_id, max(1, min(200, int(limit)))),
        )
        return [_event_from_row(dict(r)) for r in cur.fetchall()]
    except Exception:
        logger.debug("ml_pipeline events list failed", exc_info=True)
        return []
    finally:
        conn.close()


def public_pipeline(run: dict[str, Any] | None) -> dict[str, Any] | None:
    """Frontend snapshot — camelCase aliases for the Lab projection."""
    if not run:
        return None
    events = run.get("events")
    if events is None and run.get("pipeline_id"):
        events = list_pipeline_events(run["pipeline_id"])
    errors = []
    if run.get("last_error"):
        errors.append({
            "stage": run.get("stage"),
            "message": run.get("last_error"),
            "timestamp": _iso_to_ms(run.get("updated_at")),
        })
    completed = _iso_to_ms(run.get("completed_at"))
    return {
        "pipeline_id": run.get("pipeline_id"),
        "pipelineId": run.get("pipeline_id"),
        "profile": run.get("profile"),
        "stage": run.get("stage"),
        "status": run.get("status"),
        "strategy": run.get("strategy"),
        "symbol": run.get("symbol"),
        "timeframe": run.get("timeframe"),
        "training_window": (run.get("config") or {}).get("training_window_months"),
        "trainingWindow": (run.get("config") or {}).get("training_window_months"),
        "config": run.get("config") or {},
        "auto_deploy_mode": run.get("auto_deploy_mode"),
        "autoDeployMode": run.get("auto_deploy_mode"),
        "autoAdvance": True,
        "stop_after_validate": bool(run.get("stop_after_validate")),
        "stopAfterValidate": bool(run.get("stop_after_validate")),
        "pending_approval": bool(run.get("pending_approval")),
        "pendingApproval": bool(run.get("pending_approval")),
        "owned_by_server": True,
        "ownedByServer": True,
        "sweep_job_id": run.get("sweep_job_id"),
        "train_job_id": run.get("train_job_id"),
        "validate_job_id": run.get("validate_job_id"),
        "backtest_job_id": run.get("backtest_job_id"),
        "bot_id": run.get("bot_id"),
        "botId": run.get("bot_id"),
        "search_result": run.get("search_result"),
        "searchResult": run.get("search_result"),
        "train_result": run.get("train_result"),
        "trainResult": run.get("train_result"),
        "validation_result": run.get("validation_result"),
        "validationResult": run.get("validation_result"),
        "backtest_result": run.get("backtest_result"),
        "backtestResult": run.get("backtest_result"),
        "gate_result": run.get("gate_result"),
        "gateResult": run.get("gate_result"),
        "last_error": run.get("last_error"),
        "lastError": run.get("last_error"),
        "errors": errors,
        "events": events or [],
        "transitionLog": events or [],
        "stage_elapsed": run.get("stage_elapsed") or {},
        "stageElapsed": run.get("stage_elapsed") or {},
        "started_at": run.get("started_at"),
        "startedAt": _iso_to_ms(run.get("started_at")),
        "stage_started_at": run.get("stage_started_at"),
        "stageStartedAt": _iso_to_ms(run.get("stage_started_at")),
        "completed_at": run.get("completed_at"),
        "completedAt": completed,
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def create_pipeline_run(
    *,
    symbol: str,
    strategy: str,
    timeframe: str | None = None,
    config: dict | None = None,
    profile: str = "research",
    auto_deploy_mode: str = "paper",
    stop_after_validate: bool = False,
    pipeline_id: str | None = None,
) -> dict[str, Any]:
    """Insert a queued run. Does not start the asyncio runner."""
    _ensure_tables()
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe

    pid = str(pipeline_id or uuid.uuid4())
    now = _now_iso()
    prof = normalize_profile(profile)
    stop = bool(stop_after_validate)
    stage = first_stage(prof, stop_after_validate=stop)
    cfg = dict(config or {})
    tf = normalize_model_timeframe(timeframe or cfg.get("timeframe"))
    cfg.setdefault("timeframe", tf)
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO ml_pipeline_runs (
                id, profile, stage, status, strategy, symbol, timeframe,
                config_json, auto_deploy_mode, stop_after_validate,
                cancel_requested, pending_approval, started_at, stage_started_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)
            """,
            (
                pid,
                prof,
                stage,
                str(strategy or "").upper(),
                str(symbol or "").upper(),
                tf,
                _dump_json(cfg),
                normalize_auto_deploy_mode(auto_deploy_mode),
                1 if stop else 0,
                now,
                now,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    append_pipeline_event(pid, "IDLE", stage)
    return get_pipeline(pid) or {"pipeline_id": pid, "stage": stage, "status": "queued"}


def get_pipeline(pipeline_id: str, *, include_events: bool = True) -> dict[str, Any] | None:
    _ensure_tables()
    if not pipeline_id:
        return None
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM ml_pipeline_runs WHERE id = ?", (pipeline_id,))
        row = cur.fetchone()
        if not row:
            return None
        run = _run_from_row(dict(row))
    finally:
        conn.close()
    if include_events:
        run["events"] = list_pipeline_events(pipeline_id)
    return run


def latest_active_pipeline(symbol: str | None = None) -> dict[str, Any] | None:
    """Newest non-terminal run, optionally filtered by symbol."""
    _ensure_tables()
    conn = _conn()
    try:
        cur = conn.cursor()
        if symbol:
            cur.execute(
                """
                SELECT * FROM ml_pipeline_runs
                WHERE symbol = ? AND status IN ('queued', 'running', 'waiting_approval')
                ORDER BY created_at DESC LIMIT 1
                """,
                (str(symbol).upper(),),
            )
        else:
            cur.execute(
                """
                SELECT * FROM ml_pipeline_runs
                WHERE status IN ('queued', 'running', 'waiting_approval')
                ORDER BY created_at DESC LIMIT 1
                """
            )
        row = cur.fetchone()
        if not row:
            return None
        run = _run_from_row(dict(row))
    finally:
        conn.close()
    run["events"] = list_pipeline_events(run["pipeline_id"])
    return run


def append_pipeline_event(
    pipeline_id: str,
    from_stage: str | None,
    to_stage: str | None,
    *,
    elapsed_ms: int | None = None,
    error: str | None = None,
    job_id: str | None = None,
) -> None:
    _ensure_tables()
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO ml_pipeline_events (
                id, pipeline_id, from_stage, to_stage, elapsed_ms, error, job_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                pipeline_id,
                from_stage,
                to_stage,
                elapsed_ms,
                error,
                job_id,
                _now_iso(),
            ),
        )
        conn.commit()
    except Exception:
        logger.debug("ml_pipeline event insert failed", exc_info=True)
    finally:
        conn.close()


def _patch_run(pipeline_id: str, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return get_pipeline(pipeline_id)
    allowed = {
        "stage", "status", "cancel_requested", "pending_approval",
        "sweep_job_id", "train_job_id", "validate_job_id", "backtest_job_id",
        "bot_id", "search_json", "train_json", "validation_json",
        "backtest_json", "gate_json", "last_error", "stage_elapsed_json",
        "started_at", "stage_started_at", "completed_at", "config_json",
    }
    json_keys = {
        "search_result": "search_json",
        "train_result": "train_json",
        "validation_result": "validation_json",
        "backtest_result": "backtest_json",
        "gate_result": "gate_json",
        "stage_elapsed": "stage_elapsed_json",
        "config": "config_json",
    }
    mapped: dict[str, Any] = {}
    for key, value in fields.items():
        col = json_keys.get(key, key)
        if col not in allowed:
            continue
        if col.endswith("_json") and not isinstance(value, str):
            mapped[col] = _dump_json(value)
        elif col in ("cancel_requested", "pending_approval"):
            mapped[col] = 1 if value else 0
        else:
            mapped[col] = value
    if not mapped:
        return get_pipeline(pipeline_id)
    mapped["updated_at"] = _now_iso()
    sets = ", ".join(f"{k} = ?" for k in mapped)
    values = list(mapped.values()) + [pipeline_id]
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE ml_pipeline_runs SET {sets} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()
    return get_pipeline(pipeline_id)


def is_pipeline_cancel_requested(pipeline_id: str) -> bool:
    run = get_pipeline(pipeline_id, include_events=False)
    return bool(run and run.get("cancel_requested"))


def cancel_pipeline(pipeline_id: str) -> dict[str, Any] | None:
    """Cooperative cancel — stop runner, cancel child ML job if any."""
    _ensure_tables()
    run = get_pipeline(pipeline_id, include_events=False)
    if run is None:
        return None
    if run["status"] in _TERMINAL:
        return get_pipeline(pipeline_id)

    job_ids = [
        run.get("sweep_job_id"),
        run.get("train_job_id"),
        run.get("validate_job_id"),
    ]
    try:
        from app.services.bots.ml_job_store import request_ml_job_cancel

        for jid in job_ids:
            if jid:
                try:
                    request_ml_job_cancel(jid)
                except Exception:
                    logger.debug("pipeline cancel: job %s", jid, exc_info=True)
    except Exception:
        pass

    now = _now_iso()
    elapsed = _stage_elapsed_update(run, now)
    _patch_run(
        pipeline_id,
        status="cancelled",
        cancel_requested=True,
        last_error="cancelled",
        completed_at=now,
        stage_elapsed=elapsed,
    )
    append_pipeline_event(pipeline_id, run.get("stage"), "ERROR", error="cancelled")

    with _runner_lock:
        task = _runner_tasks.get(pipeline_id)
    if task is not None and not task.done():
        task.cancel()
    return get_pipeline(pipeline_id)


def approve_pipeline(pipeline_id: str) -> dict[str, Any] | None:
    """Clear the approval pause so the runner can deploy."""
    run = get_pipeline(pipeline_id, include_events=False)
    if run is None:
        return None
    if not run.get("pending_approval") and run.get("status") != "waiting_approval":
        return get_pipeline(pipeline_id)
    cfg = dict(run.get("config") or {})
    cfg["deploy_approved"] = True
    _patch_run(
        pipeline_id,
        pending_approval=False,
        status="queued",
        stage="READY_TO_DEPLOY",
        last_error=None,
        stage_started_at=_now_iso(),
        config=cfg,
    )
    append_pipeline_event(pipeline_id, run.get("stage"), "READY_TO_DEPLOY")
    return get_pipeline(pipeline_id)


def retry_pipeline(pipeline_id: str) -> dict[str, Any] | None:
    """Re-queue a failed / cancelled run from the last non-terminal stage."""
    run = get_pipeline(pipeline_id, include_events=False)
    if run is None:
        return None
    if run["status"] in ("queued", "running", "waiting_approval"):
        out = dict(run)
        out["requeued"] = 0
        return out
    stage = run.get("stage") or first_stage(run.get("profile") or "research")
    if stage in ("ERROR", "GATE_FAILED", "DEPLOYED"):
        # Retry the last working stage from events, else TRAINING.
        events = list_pipeline_events(pipeline_id)
        stage = "TRAINING"
        for ev in reversed(events):
            prev = ev.get("from")
            if prev and prev not in ("ERROR", "GATE_FAILED", "DEPLOYED", "IDLE"):
                stage = prev
                break
    _patch_run(
        pipeline_id,
        status="queued",
        stage=stage,
        cancel_requested=False,
        pending_approval=False,
        last_error=None,
        completed_at=None,
        stage_started_at=_now_iso(),
    )
    out = get_pipeline(pipeline_id)
    if out:
        out["requeued"] = 1
    return out


def _stage_elapsed_update(run: dict, now_iso: str) -> dict:
    elapsed = dict(run.get("stage_elapsed") or {})
    started = _iso_to_ms(run.get("stage_started_at"))
    now_ms = _iso_to_ms(now_iso)
    if started is not None and now_ms is not None and run.get("stage"):
        elapsed[run["stage"]] = max(0, now_ms - started)
    return elapsed


def _job_id_field(stage: str) -> str | None:
    return {
        "SEARCH": "sweep_job_id",
        "TRAINING": "train_job_id",
        "VALIDATING": "validate_job_id",
        "BACKTESTING": "backtest_job_id",
    }.get(stage)


def _result_field(stage: str) -> str | None:
    return {
        "SEARCH": "search_result",
        "TRAINING": "train_result",
        "VALIDATING": "validation_result",
        "BACKTESTING": "backtest_result",
        "GATE_CHECK": "gate_result",
    }.get(stage)


# ---------------------------------------------------------------------------
# Crash recovery + runner
# ---------------------------------------------------------------------------


def recover_interrupted_pipelines() -> list[str]:
    """Mark orphaned in-flight stages; return ids that still have work."""
    _ensure_tables()
    conn = _conn()
    resume: list[str] = []
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM ml_pipeline_runs WHERE status IN ('queued', 'running')"
        )
        rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        logger.warning("ml_pipeline recovery scan failed", exc_info=True)
        raise
    finally:
        conn.close()

    now = _now_iso()
    for row in rows:
        run = _run_from_row(row)
        pid = run.get("pipeline_id")
        if not pid:
            continue
        if run.get("cancel_requested"):
            _patch_run(pid, status="cancelled", completed_at=now, last_error="cancelled")
            continue
        stage = run.get("stage")
        field = _job_id_field(stage or "")
        job_id = run.get(field) if field else None
        if job_id:
            try:
                from app.services.bots.ml_job_store import get_ml_job

                job = get_ml_job(job_id)
            except Exception:
                job = None
            if job and job.get("status") == "done":
                result_key = _result_field(stage or "")
                patch = {"status": "queued", "stage_started_at": now}
                if result_key:
                    patch[result_key] = job.get("result")
                nxt = next_stage(
                    run["profile"], stage,
                    stop_after_validate=run.get("stop_after_validate"),
                )
                if nxt:
                    patch["stage"] = nxt
                    append_pipeline_event(pid, stage, nxt, job_id=job_id)
                else:
                    patch["status"] = "done"
                    patch["completed_at"] = now
                _patch_run(pid, **patch)
                if patch.get("status") == "queued":
                    resume.append(pid)
                continue
            if job and job.get("status") in ("error", "cancelled"):
                _patch_run(
                    pid,
                    status="failed",
                    last_error=str(job.get("error") or "child job failed"),
                    completed_at=now,
                )
                continue
            # Job still running in RAM — or interrupted without a finish.
            try:
                from app.services.bots.ml_job_store import load_ml_job_checkpoint

                cp = load_ml_job_checkpoint(job_id)
                if isinstance(cp, dict) and cp.get("resume_ok"):
                    _patch_run(pid, status="queued")
                    resume.append(pid)
                    continue
            except Exception:
                pass
            _patch_run(
                pid,
                status="failed",
                last_error="server restarted",
                completed_at=now,
            )
            append_pipeline_event(pid, stage, "ERROR", error="server restarted", job_id=job_id)
            continue
        # No child job yet — safe to resume the stage.
        if run.get("status") == "running":
            _patch_run(pid, status="queued")
        resume.append(pid)
    return resume


def resume_incomplete_pipelines(state: Any = None, *, event_bus: Any = None) -> list[str]:
    global _resumed
    with _runner_lock:
        if _resumed:
            return []
        _resumed = True
    try:
        recovered = recover_interrupted_pipelines()
    except Exception:
        with _runner_lock:
            _resumed = False
        logger.warning("ML pipeline recovery failed — will retry on next request", exc_info=True)
        return []
    started = []
    for pid in recovered:
        if start_pipeline_runner(pid, state, event_bus=event_bus):
            started.append(pid)
    if started:
        logger.info("ML pipeline runner resumed %d interrupted run(s)", len(started))
    return started


def start_pipeline_runner(
    pipeline_id: str,
    state: Any = None,
    *,
    event_bus: Any = None,
    stage_executor: StageExecutor | None = None,
) -> bool:
    """Spawn the asyncio driver if one is not already live."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    with _runner_lock:
        existing = _runner_tasks.get(pipeline_id)
        if existing is not None and not existing.done():
            return False

        async def _wrapped() -> None:
            try:
                await run_pipeline(
                    pipeline_id,
                    state=state,
                    event_bus=event_bus,
                    stage_executor=stage_executor,
                )
            except asyncio.CancelledError:
                logger.warning("ML pipeline runner %s cancelled", pipeline_id)
                raise
            except Exception:
                logger.exception("ML pipeline runner %s crashed", pipeline_id)
                _patch_run(
                    pipeline_id,
                    status="failed",
                    last_error="pipeline runner crashed",
                    completed_at=_now_iso(),
                )
            finally:
                with _runner_lock:
                    _runner_tasks.pop(pipeline_id, None)

        task = loop.create_task(_wrapped())
        _runner_tasks[pipeline_id] = task
        return True


def ensure_pipeline_runner(
    pipeline_id: str,
    state: Any = None,
    *,
    event_bus: Any = None,
) -> bool:
    run = get_pipeline(pipeline_id, include_events=False)
    if run is None or run["status"] not in ("queued", "running"):
        return False
    return start_pipeline_runner(pipeline_id, state, event_bus=event_bus)


async def _publish_pipeline(event_bus: Any, run: dict | None) -> None:
    if event_bus is None or not run:
        return
    try:
        from app.api.outbound import frame
        from app.api.protocol import MessageType
        from app.services.events import channels

        payload = frame(MessageType.ML_PIPELINE, public_pipeline(run))
        await event_bus.publish(channels.WS_BROADCAST, payload)
    except Exception:
        logger.debug("ml_pipeline publish failed", exc_info=True)


async def run_pipeline(
    pipeline_id: str,
    *,
    state: Any = None,
    event_bus: Any = None,
    stage_executor: StageExecutor | None = None,
) -> dict[str, Any] | None:
    """Drive remaining stages until terminal, approval pause, or cancel."""
    _ensure_tables()
    executor = stage_executor or (
        lambda run, stage: execute_pipeline_stage(state, run, stage, event_bus=event_bus)
    )

    while True:
        run = get_pipeline(pipeline_id)
        if run is None:
            return None
        if run.get("cancel_requested") or run["status"] == "cancelled":
            return run
        if run["status"] in _TERMINAL:
            return run
        if run.get("pending_approval") or run["status"] == "waiting_approval":
            return run

        stage = run.get("stage") or first_stage(run["profile"])
        if stage in ("DEPLOYED", "GATE_FAILED", "ERROR"):
            status = "done" if stage == "DEPLOYED" else (
                "gate_failed" if stage == "GATE_FAILED" else "failed"
            )
            return _patch_run(pipeline_id, status=status, completed_at=_now_iso())

        _patch_run(pipeline_id, status="running", stage=stage, stage_started_at=_now_iso())
        await _publish_pipeline(event_bus, get_pipeline(pipeline_id))

        try:
            result = await executor(get_pipeline(pipeline_id, include_events=False) or run, stage)
        except asyncio.CancelledError:
            _patch_run(pipeline_id, status="cancelled", last_error="cancelled", completed_at=_now_iso())
            raise
        except Exception as exc:
            logger.exception("ML pipeline %s stage %s raised", pipeline_id, stage)
            result = {"ok": False, "error": str(exc)}

        if not isinstance(result, dict):
            result = {"ok": False, "error": "invalid stage result"}

        if is_pipeline_cancel_requested(pipeline_id) or result.get("cancelled"):
            now = _now_iso()
            live = get_pipeline(pipeline_id, include_events=False) or run
            _patch_run(
                pipeline_id,
                status="cancelled",
                last_error="cancelled",
                completed_at=now,
                stage_elapsed=_stage_elapsed_update(live, now),
            )
            append_pipeline_event(pipeline_id, stage, "ERROR", error="cancelled")
            return get_pipeline(pipeline_id)

        now = _now_iso()
        live = get_pipeline(pipeline_id, include_events=False) or run
        elapsed = _stage_elapsed_update(live, now)
        job_id = result.get("job_id")
        patch: dict[str, Any] = {"stage_elapsed": elapsed}

        field = _job_id_field(stage)
        if field and job_id:
            patch[field] = job_id
        result_key = _result_field(stage)
        if result_key and result.get("result") is not None:
            patch[result_key] = result.get("result")

        if result.get("awaiting_approval"):
            patch.update(
                status="waiting_approval",
                pending_approval=True,
                stage="READY_TO_DEPLOY",
                last_error=None,
            )
            _patch_run(pipeline_id, **patch)
            append_pipeline_event(pipeline_id, stage, "READY_TO_DEPLOY", job_id=job_id)
            await _publish_pipeline(event_bus, get_pipeline(pipeline_id))
            return get_pipeline(pipeline_id)

        if stage == "GATE_CHECK" and (result.get("blocking") or not result.get("ok")):
            reason = str(result.get("error") or "Deploy gate blocked")
            patch.update(
                status="gate_failed",
                stage="GATE_FAILED",
                last_error=reason,
                completed_at=now,
            )
            _patch_run(pipeline_id, **patch)
            append_pipeline_event(pipeline_id, stage, "GATE_FAILED", error=reason, job_id=job_id)
            await _publish_pipeline(event_bus, get_pipeline(pipeline_id))
            return get_pipeline(pipeline_id)

        if not result.get("ok"):
            reason = str(result.get("error") or f"{stage} failed")
            patch.update(
                status="failed",
                stage="ERROR" if stage != "GATE_CHECK" else "GATE_FAILED",
                last_error=reason,
                completed_at=now,
            )
            _patch_run(pipeline_id, **patch)
            append_pipeline_event(
                pipeline_id, stage, patch["stage"], error=reason, job_id=job_id,
            )
            await _publish_pipeline(event_bus, get_pipeline(pipeline_id))
            return get_pipeline(pipeline_id)

        if stage == "READY_TO_DEPLOY" or result.get("deployed"):
            bot_id = result.get("bot_id")
            patch.update(
                status="done",
                stage="DEPLOYED",
                completed_at=now,
                last_error=None,
            )
            if bot_id:
                patch["bot_id"] = bot_id
            _patch_run(pipeline_id, **patch)
            append_pipeline_event(pipeline_id, stage, "DEPLOYED", job_id=job_id)
            await _publish_pipeline(event_bus, get_pipeline(pipeline_id))
            return get_pipeline(pipeline_id)

        nxt = next_stage(
            live["profile"],
            stage,
            stop_after_validate=live.get("stop_after_validate"),
        )
        if nxt is None:
            # stop_after_validate completes on VALIDATING.
            patch.update(status="done", completed_at=now, last_error=None)
            _patch_run(pipeline_id, **patch)
            append_pipeline_event(pipeline_id, stage, f"{stage}:done", job_id=job_id)
            await _publish_pipeline(event_bus, get_pipeline(pipeline_id))
            return get_pipeline(pipeline_id)

        patch.update(status="queued", stage=nxt, last_error=None, stage_started_at=now)
        _patch_run(pipeline_id, **patch)
        append_pipeline_event(pipeline_id, stage, nxt, job_id=job_id)
        await _publish_pipeline(event_bus, get_pipeline(pipeline_id))


# ---------------------------------------------------------------------------
# Default stage executors (real engines)
# ---------------------------------------------------------------------------


def research_stage_config(config: dict | None, *, profile: str) -> dict[str, Any]:
    """Force calendar + DQ + research PBO on pipeline jobs."""
    cfg = dict(config or {})
    cfg["ml_calendar_holdout"] = True
    cfg["dq_train_gate"] = cfg.get("dq_train_gate") or "block"
    if str(cfg.get("dq_train_gate")).lower() in ("warn", "off", "false", "0"):
        cfg["dq_train_gate"] = "block"
    cfg["pbo_profile"] = "research"
    cfg["force_pbo"] = True
    cfg["sim_mode"] = cfg.get("sim_mode") or "live_aligned"
    if normalize_profile(profile) == "retrain":
        cfg.setdefault("retrain_from_live_model", True)
        cfg.setdefault("use_optimized_hyperparams", True)
        cfg.setdefault("champion_train", True)
    return cfg


async def execute_pipeline_stage(
    state: Any,
    run: dict,
    stage: str,
    *,
    event_bus: Any = None,
) -> dict[str, Any]:
    if state is None:
        return {"ok": False, "error": "pipeline runner has no app state"}
    if is_pipeline_cancel_requested(run.get("pipeline_id")):
        return {"ok": False, "cancelled": True, "error": "cancelled"}

    cfg = research_stage_config(run.get("config"), profile=run.get("profile") or "research")
    cfg["pipeline_id"] = run.get("pipeline_id")
    cfg.setdefault("timeframe", run.get("timeframe"))
    cfg.setdefault("symbol", run.get("symbol"))
    cfg.setdefault("model_symbol", run.get("symbol"))

    if stage == "SEARCH":
        return await _execute_search(state, run, cfg, event_bus=event_bus)
    if stage == "TRAINING":
        return await _execute_train(state, run, cfg, event_bus=event_bus)
    if stage == "VALIDATING":
        return await _execute_validate(state, run, cfg, event_bus=event_bus)
    if stage == "BACKTESTING":
        return await _execute_backtest(state, run, cfg, event_bus=event_bus)
    if stage == "GATE_CHECK":
        return _execute_gate(run, cfg)
    if stage == "READY_TO_DEPLOY":
        return await _execute_deploy(state, run, cfg)
    return {"ok": False, "error": f"unknown stage {stage}"}


async def _fetch_and_enrich(
    state: Any,
    symbol: str,
    strategy: str,
    cfg: dict,
    *,
    purpose: str,
) -> tuple[list, dict]:
    from app.api.http.app import _enrich_training_candles, _fetch_training_candles
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe
    from app.services.bots.ml_training_window import (
        bar_limit_for_training_window,
        parse_training_window_months,
        summarize_training_window,
    )

    tf = normalize_model_timeframe(cfg.get("timeframe"))
    win_months = parse_training_window_months(cfg)
    bar_limit = bar_limit_for_training_window(win_months, timeframe=tf, purpose=purpose)
    fetch_cfg = dict(cfg)
    candles = await _fetch_training_candles(
        state, symbol, tf=tf, months=win_months, limit=bar_limit, config=fetch_cfg,
    )
    if len(candles) < 200:
        raise ValueError(f"insufficient candles ({len(candles)})")
    candles = await asyncio.to_thread(_enrich_training_candles, symbol, candles, strategy, cfg)
    window = summarize_training_window(
        candles,
        win_months,
        bar_limit=bar_limit,
        timeframe=tf,
        calendar=cfg.get("_data_calendar") if isinstance(cfg.get("_data_calendar"), dict) else None,
    )
    cfg["_training_window"] = window
    return candles, cfg


async def _execute_search(state, run, cfg, *, event_bus=None) -> dict[str, Any]:
    from app.services.bots.ml_hyperparam_sweep import SWEEPABLE_ML_STRATEGIES
    from app.services.bots.ml_train_executor import submit_hyperparam_sweep_job

    strategy = str(run.get("strategy") or "").upper()
    symbol = str(run.get("symbol") or "").upper()
    if strategy not in SWEEPABLE_ML_STRATEGIES:
        return {"ok": True, "result": {"ok": True, "skipped": True, "reason": "not sweepable"}}
    try:
        candles, cfg = await _fetch_and_enrich(state, symbol, strategy, cfg, purpose="train")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    result = await submit_hyperparam_sweep_job(
        strategy, symbol, candles, cfg, event_bus=event_bus,
    )
    out = dict(result) if isinstance(result, dict) else {"ok": False, "error": "invalid sweep"}
    job_id = out.get("job_id")
    if out.get("ok") and isinstance(out.get("best_hyperparams"), dict):
        merged = dict(run.get("config") or {})
        merged.update(out["best_hyperparams"])
        merged["optimization_run_id"] = out.get("optimization_run_id")
        _patch_run(run["pipeline_id"], config=merged)
    return {"ok": bool(out.get("ok")), "error": out.get("error"), "result": out, "job_id": job_id}


async def _execute_train(state, run, cfg, *, event_bus=None) -> dict[str, Any]:
    from app.services.bots.ml_train_executor import submit_train_job
    from app.services.bots.ml_training_window import prepare_lab_champion_train_config
    from app.services.bots.optimization_store import (
        merge_live_model_train_hyperparams,
        merge_optimized_train_hyperparams,
    )

    strategy = str(run.get("strategy") or "").upper()
    symbol = str(run.get("symbol") or "").upper()
    cfg = prepare_lab_champion_train_config(cfg)
    search = run.get("search_result") if isinstance(run.get("search_result"), dict) else {}
    best = search.get("best_hyperparams") if isinstance(search.get("best_hyperparams"), dict) else {}
    if best:
        cfg.update(best)
    if cfg.get("retrain_from_live_model"):
        cfg = merge_live_model_train_hyperparams(
            cfg, symbol, strategy, timeframe=cfg.get("timeframe"),
        )
    cfg = merge_optimized_train_hyperparams(cfg, symbol, strategy, require_opt_in=True)
    cfg = prepare_lab_champion_train_config(cfg)
    try:
        candles, cfg = await _fetch_and_enrich(state, symbol, strategy, cfg, purpose="train")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    result = await submit_train_job(strategy, symbol, candles, cfg, event_bus=event_bus)
    out = dict(result) if isinstance(result, dict) else {"ok": False, "error": "invalid train"}
    return {
        "ok": bool(out.get("ok") and not out.get("cancelled")),
        "cancelled": bool(out.get("cancelled")),
        "error": out.get("error"),
        "result": out,
        "job_id": out.get("job_id"),
    }


async def _execute_validate(state, run, cfg, *, event_bus=None) -> dict[str, Any]:
    from app.services.bots.ml_job_store import create_ml_job
    from app.services.bots.ml_train_executor import submit_validate_job

    strategy = str(run.get("strategy") or "").upper()
    symbol = str(run.get("symbol") or "").upper()
    try:
        n_folds = max(1, min(10, int(cfg.get("validate_folds") or cfg.get("n_folds") or 5)))
    except (TypeError, ValueError):
        n_folds = 5
    mode = str(cfg.get("validate_mode") or "rolling").lower()
    try:
        pbo_segments = max(2, min(12, int(cfg.get("pbo_segments") or 6)))
    except (TypeError, ValueError):
        pbo_segments = 6
    cfg["wf_capacity_parity"] = True
    cfg["pbo_profile"] = "research"
    cfg["force_pbo"] = True
    # Record the job id before submit returns — otherwise cancel/UI cannot
    # find a VALIDATING run whose worker is stuck inside a fold.
    jid = create_ml_job(kind="validate", strategy=strategy, symbol=symbol)
    pid = str(run.get("pipeline_id") or "")
    if pid:
        _patch_run(pid, validate_job_id=jid)
    try:
        candles, cfg = await _fetch_and_enrich(state, symbol, strategy, cfg, purpose="validate")
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "job_id": jid}
    result = await submit_validate_job(
        strategy, symbol, candles, cfg,
        n_folds=n_folds, mode=mode, run_pbo=True, pbo_segments=pbo_segments,
        event_bus=event_bus,
        job_id=jid,
    )
    out = dict(result) if isinstance(result, dict) else {"ok": False, "error": "invalid validate"}
    return {
        "ok": bool(out.get("ok") and not out.get("cancelled")),
        "cancelled": bool(out.get("cancelled")),
        "error": out.get("error"),
        "result": out,
        "job_id": out.get("job_id") or jid,
    }


async def _execute_backtest(state, run, cfg, *, event_bus=None) -> dict[str, Any]:
    from app.api.http.app import _enrich_training_candles, _fetch_training_candles
    from app.services.bots.backtest_store import save_backtest_run
    from app.services.bots.ml_data_calendar import (
        build_ml_data_calendar,
        calendar_from_config,
        default_holdout_days,
        trim_candles_to_holdout,
    )
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe
    from app.services.bots.ml_training_window import (
        bar_limit_for_training_window,
        parse_training_window_months,
    )

    strategy = str(run.get("strategy") or "").upper()
    symbol = str(run.get("symbol") or "").upper()
    tf = normalize_model_timeframe(cfg.get("timeframe") or run.get("timeframe"))
    win_months = parse_training_window_months(cfg)
    # Full window (not FIT-trimmed) so holdout bars exist.
    fetch_cfg = {**cfg, "ml_calendar_holdout": False}
    bar_limit = bar_limit_for_training_window(win_months, timeframe=tf, purpose="train")
    candles = await _fetch_training_candles(
        state, symbol, tf=tf, months=win_months, limit=bar_limit, config=fetch_cfg,
    )
    if len(candles) < 50:
        return {"ok": False, "error": f"insufficient candles for holdout ({len(candles)})"}
    candles = await asyncio.to_thread(_enrich_training_candles, symbol, candles, strategy, cfg)
    calendar = calendar_from_config(cfg, months=win_months, timeframe=tf)
    if not calendar:
        calendar = build_ml_data_calendar(months=win_months, timeframe=tf, config=cfg)
    holdout = trim_candles_to_holdout(candles, calendar)
    if len(holdout) < 50:
        holdout = candles[-min(len(candles), 400):]
    bt = getattr(state, "backtester", None)
    if bt is None or not hasattr(bt, "run_backtest"):
        return {"ok": False, "error": "Backtester not available"}
    bt_cfg = {
        **cfg,
        "sim_mode": "live_aligned",
        "ml_backtest_range": "holdout",
        "timeframe": tf,
        "strategy": strategy,
    }
    results = await asyncio.to_thread(bt.run_backtest, symbol, strategy, bt_cfg, holdout)
    if not isinstance(results, dict):
        return {"ok": False, "error": "invalid backtest result"}
    if results.get("error") and not results.get("trade_count"):
        return {"ok": False, "error": str(results.get("error")), "result": results}
    days = int(calendar.get("holdout_days") or default_holdout_days(win_months))
    try:
        run_id = save_backtest_run(symbol, strategy, bt_cfg, days, results)
        results["run_id"] = run_id
    except Exception:
        logger.debug("pipeline holdout persist failed", exc_info=True)
        run_id = None
    return {"ok": True, "result": results, "job_id": run_id}


def _execute_gate(run: dict, cfg: dict) -> dict[str, Any]:
    from app.services.bots.deploy_gate import evaluate_deploy_gate

    results = run.get("backtest_result") if isinstance(run.get("backtest_result"), dict) else {}
    gate = evaluate_deploy_gate(
        results,
        symbol=run.get("symbol"),
        run_config={**cfg, "strategy": run.get("strategy"), "timeframe": run.get("timeframe")},
        run_days=(results.get("days") if isinstance(results, dict) else None),
        run_timeframe=run.get("timeframe"),
    )
    blocking = bool(gate.get("blocking") and not gate.get("passed"))
    return {
        "ok": not blocking,
        "blocking": blocking,
        "error": gate.get("block_reason") if blocking else None,
        "result": gate,
    }


async def _execute_deploy(state, run, cfg) -> dict[str, Any]:
    mode = normalize_auto_deploy_mode(run.get("auto_deploy_mode"))
    approved = bool((run.get("config") or {}).get("deploy_approved") or cfg.get("deploy_approved"))
    if mode == "approval" and not approved:
        return {"ok": True, "awaiting_approval": True}

    if mode == "paper" and not is_paper_execution(state, cfg.get("execution_mode")):
        return {"ok": True, "awaiting_approval": True}

    manager = getattr(state, "bot_manager", None)
    if manager is None or not hasattr(manager, "create_bot"):
        return {"ok": False, "error": "Bot manager not available"}

    try:
        allocation = float(cfg.get("allocation") or 1000)
    except (TypeError, ValueError):
        allocation = 1000.0
    deploy_cfg = dict(cfg)
    gate = run.get("gate_result") if isinstance(run.get("gate_result"), dict) else {}
    if gate:
        deploy_cfg["pipeline_gate"] = {
            "passed": gate.get("passed"),
            "blocking": gate.get("blocking"),
        }
    bt = run.get("backtest_result") if isinstance(run.get("backtest_result"), dict) else {}
    if bt.get("run_id"):
        deploy_cfg["backtest_run_id"] = bt["run_id"]
    deploy_cfg["pipeline_id"] = run.get("pipeline_id")
    deploy_cfg["pipeline_source"] = "ml_lab"
    try:
        bot = await manager.create_bot(
            str(run.get("strategy") or ""),
            str(run.get("symbol") or ""),
            str(run.get("timeframe") or cfg.get("timeframe") or "1m"),
            allocation,
            deploy_cfg,
            execution_mode=str(cfg.get("bot_execution_mode") or "BAR_CLOSE"),
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    bot_id = None
    if isinstance(bot, dict):
        bot_id = bot.get("id") or bot.get("bot_id")
    elif hasattr(bot, "get"):
        bot_id = bot.get("id")
    return {"ok": True, "deployed": True, "bot_id": bot_id, "result": {"bot_id": bot_id}}


def apply_search_hyperparams_to_config(config: dict, search_result: dict | None) -> dict:
    cfg = dict(config or {})
    if isinstance(search_result, dict) and isinstance(search_result.get("best_hyperparams"), dict):
        cfg.update(search_result["best_hyperparams"])
    return cfg
