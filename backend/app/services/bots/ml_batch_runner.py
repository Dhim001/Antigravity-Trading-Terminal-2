"""Durable ML batch training runner (ML Lab Phase 2).

Persists batch + item state in SQLite (``ml_batches`` / ``ml_batch_items``)
and executes items through the existing process-isolated train/validate path
(``submit_train_job`` / ``submit_validate_job``) so DQ gates, RSS limits, and
progress reporting still apply. Supports idempotent create, cooperative
cancel via the existing job-store cancel path, retry of failed items, and
crash recovery — items orphaned mid-flight are marked ``error: server
restarted`` and batches with pending work resume on startup / first request.

Phase 4 adds intelligent scheduling: cost-based item ordering (heavy deep/RL
strategies run last under the default ``cost_asc`` schedule), optional
parallel workers capped by ``resolve_ml_train_max_workers()``, and transient
error retry with exponential backoff. Concurrency safety: workers are asyncio
tasks on one event loop, item claiming is synchronous SQLite (no await between
SELECT and UPDATE), and the job store / process pool already guard shared
state with locks — no extra locking is needed here.
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

BATCH_STATUSES = frozenset({"queued", "running", "done", "failed", "cancelled"})
ITEM_STATUSES = frozenset({"pending", "running", "done", "error", "cancelled", "skipped"})
_BATCH_TERMINAL = frozenset({"done", "failed", "cancelled"})
_ITEM_TERMINAL = frozenset({"done", "error", "cancelled", "skipped"})
MAX_BATCH_ITEMS = 50

# Phase 4 — scheduling strategies for item ordering at create time.
BATCH_SCHEDULES = frozenset({"fifo", "cost_asc", "cost_desc"})
DEFAULT_BATCH_SCHEDULE = "cost_asc"

# Relative training cost weights. Deep/RL trainers peak GPU+RSS inside worker
# processes (HIGH), GBM/tree trainers are MEDIUM, everything else LOW.
TRAIN_COST_LOW = 1
TRAIN_COST_MEDIUM = 2
TRAIN_COST_HIGH = 3

# Transient item failures are retried in place before marking ``error``:
# up to ITEM_MAX_RETRIES extra attempts with ITEM_RETRY_BACKOFF_SEC delays.
ITEM_MAX_RETRIES = 2
ITEM_RETRY_BACKOFF_SEC = (30.0, 120.0)

# A live batch with pending work but no running item and no status movement
# for this long is stalled (runner task gone or wedged). Between-items gaps
# are milliseconds, so 5 minutes is unambiguous.
STALL_THRESHOLD_SEC = 300.0

# Minimum seconds between ensure_batch_runner respawn attempts for the same
# batch — breaks poll-driven crash loops.
RESPAWN_COOLDOWN_SEC = 30.0

_TRANSIENT_ERROR_TOKENS = (
    "brokenprocesspool",
    "process pool is not usable",
    "terminated abruptly",
    "child process",
    "429",
    "rate limit",
    "too many requests",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "temporary network",
    "connection reset",
    "connection refused",
    "econnreset",
    "econnrefused",
    "network error",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "http 502",
    "http 503",
    "http 504",
)

_PERMANENT_ERROR_TOKENS = (
    "insufficient",
    "data-quality gate",
    "dq gate",
    "unsupported",
    "not supported",
    "no module named",
    "modulenotfound",
    "importerror",
    "missing dependency",
    "not installed",
    "need >=",
    "invalid",
)

_runner_lock = threading.Lock()
_runner_tasks: dict[str, asyncio.Task] = {}
_last_spawn_attempt: dict[str, float] = {}
_tables_ready = False
_resumed = False

# Async callable (batch, item) -> result dict; injectable for tests.
ItemExecutor = Callable[[dict, dict], Awaitable[dict]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
            from app.database import ensure_ml_batch_tables

            ensure_ml_batch_tables()
        except Exception:
            logger.debug("ml_batch table ensure failed", exc_info=True)
        _tables_ready = True


def reset_ml_batch_runner_for_tests() -> None:
    global _tables_ready, _resumed
    with _runner_lock:
        _runner_tasks.clear()
        _last_spawn_attempt.clear()
        _tables_ready = False
        _resumed = False


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------


def _batch_from_row(row: dict) -> dict[str, Any]:
    return {
        "batch_id": row.get("id"),
        "symbol": row.get("symbol"),
        "status": row.get("status") or "queued",
        "total": int(row.get("total") or 0),
        "completed": int(row.get("completed") or 0),
        "failed": int(row.get("failed") or 0),
        "cancelled": int(row.get("cancelled") or 0),
        "fail_fast": bool(row.get("fail_fast")),
        "concurrency": int(row.get("concurrency") or 1),
        "schedule": normalize_batch_schedule(row.get("schedule")),
        "cancel_requested": bool(row.get("cancel_requested")),
        "idempotency_key": row.get("idempotency_key"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _item_from_row(row: dict) -> dict[str, Any]:
    seq = int(row.get("seq") or 0)
    return {
        "item_id": row.get("id"),
        "batch_id": row.get("batch_id"),
        "seq": seq,
        # Execution position under the batch's schedule (identical to seq;
        # exposed separately so clients can render the planned order).
        "order": seq,
        "cost": strategy_train_cost(row.get("strategy")),
        "strategy": row.get("strategy"),
        "config": _load_item_config(row.get("config_json")),
        "validate_after": bool(row.get("validate_after")),
        "status": row.get("status") or "pending",
        "job_id": row.get("job_id"),
        "error": row.get("error"),
        "retry_count": int(row.get("retry_count") or 0),
        "last_error": row.get("last_error"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _load_item_config(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Phase 4 — cost-based ordering + transient error classification
# ---------------------------------------------------------------------------


def normalize_batch_schedule(schedule: Any) -> str:
    """Coerce a schedule param into a known value (default ``cost_asc``)."""
    sched = str(schedule or "").strip().lower()
    return sched if sched in BATCH_SCHEDULES else DEFAULT_BATCH_SCHEDULE


def strategy_train_cost(strategy: Any) -> int:
    """Relative training cost weight for a strategy (Phase 4 scheduling)."""
    strat = str(strategy or "").upper()
    if not strat:
        return TRAIN_COST_LOW
    try:
        from app.services.bots.ml_train_executor import TORCH_TRAIN_STRATEGIES
    except Exception:
        TORCH_TRAIN_STRATEGIES = frozenset()
    if strat in TORCH_TRAIN_STRATEGIES:
        return TRAIN_COST_HIGH
    if strat == "ML_SIGNAL_BOOST":
        return TRAIN_COST_MEDIUM
    return TRAIN_COST_LOW


def order_batch_items(items: list[dict], schedule: Any = DEFAULT_BATCH_SCHEDULE) -> list[dict]:
    """Order create-time item rows per the schedule.

    ``cost_asc`` (default) runs cheap strategies first and heavy deep/RL
    trainers last — lighter jobs are not blocked behind a possible OOM, and
    peak worker memory stays low early in the batch. Sorts are stable, so
    equal-cost items keep their submission order under every schedule.
    """
    rows = list(items or [])
    sched = normalize_batch_schedule(schedule)
    if sched == "fifo":
        return rows
    return sorted(
        rows,
        key=lambda it: strategy_train_cost(it.get("strategy")),
        reverse=(sched == "cost_desc"),
    )


def is_transient_batch_error(error: Any, result: dict | None = None) -> bool:
    """Classify an item failure as transient (worth retrying) or permanent.

    Trainers may force the decision with ``result["error_kind"]`` /
    ``result["transient"]`` / ``result["permanent"]``; otherwise the error
    text is matched against known transient infrastructure failures (broken
    process pool, 429/rate limit, timeouts, temporary network) vs permanent
    ones (insufficient data, DQ gate, unsupported strategy, missing
    dependency). Unknown errors default to permanent — never re-run a config
    that is likely broken.
    """
    if isinstance(result, dict):
        kind = str(result.get("error_kind") or "").strip().lower()
        if kind == "transient" or result.get("transient") is True:
            return True
        if kind == "permanent" or result.get("permanent") is True:
            return False
    msg = str(error or "").lower()
    if not msg:
        return False
    for token in _PERMANENT_ERROR_TOKENS:
        if token in msg:
            return False
    return any(token in msg for token in _TRANSIENT_ERROR_TOKENS)


def _retry_backoff_seconds(retry_number: int) -> float:
    """Delay before retry ``retry_number`` (1-based); last value repeats."""
    seq = ITEM_RETRY_BACKOFF_SEC
    if not seq:
        return 0.0
    idx = min(max(1, int(retry_number)), len(seq)) - 1
    try:
        return max(0.0, float(seq[idx]))
    except (TypeError, ValueError, IndexError):
        return 0.0


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def create_batch(
    symbol: str,
    items: list[dict],
    *,
    concurrency: int = 1,
    fail_fast: bool = False,
    idempotency_key: str | None = None,
    schedule: str = DEFAULT_BATCH_SCHEDULE,
) -> tuple[dict[str, Any], bool]:
    """Insert batch + items. Returns ``(batch, created)``.

    When ``idempotency_key`` matches an existing batch the original is
    returned with ``created=False`` (also on a unique-index race). Items are
    persisted in execution order: ``schedule`` = ``fifo`` keeps submission
    order, ``cost_asc`` (default) runs cheap strategies first / heavy last,
    ``cost_desc`` the reverse.
    """
    _ensure_tables()
    idem = str(idempotency_key).strip() if idempotency_key else None
    if idem:
        existing = get_batch_by_idempotency_key(idem)
        if existing:
            return existing, False

    batch_id = str(uuid.uuid4())
    now = _now_iso()
    sched = normalize_batch_schedule(schedule)
    rows = order_batch_items(list(items or [])[:MAX_BATCH_ITEMS], sched)
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO ml_batches (
                id, symbol, status, total, completed, failed, cancelled,
                fail_fast, concurrency, schedule, cancel_requested,
                idempotency_key, created_at, updated_at
            ) VALUES (?, ?, 'queued', ?, 0, 0, 0, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                batch_id,
                str(symbol or "").upper(),
                len(rows),
                1 if fail_fast else 0,
                max(1, int(concurrency or 1)),
                sched,
                idem,
                now,
                now,
            ),
        )
        for seq, item in enumerate(rows):
            cur.execute(
                """
                INSERT INTO ml_batch_items (
                    id, batch_id, seq, strategy, config_json, validate_after,
                    status, job_id, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    batch_id,
                    seq,
                    str(item.get("strategy") or "").upper(),
                    json.dumps(item.get("config") or {}, default=str),
                    1 if item.get("validate_after") else 0,
                    now,
                    now,
                ),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        if idem:
            existing = get_batch_by_idempotency_key(idem)
            if existing:
                return existing, False
        raise
    finally:
        conn.close()
    batch = get_batch(batch_id)
    assert batch is not None
    return batch, True


def get_batch_by_idempotency_key(idempotency_key: str) -> dict[str, Any] | None:
    _ensure_tables()
    idem = str(idempotency_key or "").strip()
    if not idem:
        return None
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM ml_batches WHERE idempotency_key = ?", (idem,))
        row = cur.fetchone()
    except Exception:
        logger.debug("ml_batch idempotency lookup failed", exc_info=True)
        return None
    finally:
        conn.close()
    if not row:
        return None
    return get_batch(dict(row).get("id"))


def get_batch(batch_id: str, *, with_items: bool = True) -> dict[str, Any] | None:
    _ensure_tables()
    if not batch_id:
        return None
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, symbol, status, total, completed, failed, cancelled,
                   fail_fast, concurrency, schedule, cancel_requested,
                   idempotency_key, created_at, updated_at
            FROM ml_batches WHERE id = ?
            """,
            (batch_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        batch = _batch_from_row(dict(row))
        if with_items:
            cur.execute(
                """
                SELECT id, batch_id, seq, strategy, config_json, validate_after,
                       status, job_id, error, retry_count, last_error,
                       created_at, updated_at
                FROM ml_batch_items WHERE batch_id = ? ORDER BY seq
                """,
                (batch_id,),
            )
            batch["items"] = [_item_from_row(dict(r)) for r in cur.fetchall()]
        return batch
    except Exception:
        logger.debug("ml_batch get failed", exc_info=True)
        return None
    finally:
        conn.close()


def _derive_batch_status(
    cancel_requested: bool,
    pending: int,
    running: int,
    completed: int,
    failed: int,
) -> str:
    if cancel_requested:
        return "cancelled"
    if running > 0:
        return "running"
    if pending > 0:
        # Waiting on a worker slot — either never started or between items.
        return "queued"
    if failed > 0 and completed == 0:
        return "failed"
    return "done"


def _refresh_batch_status(batch_id: str) -> dict[str, Any] | None:
    """Recompute counts + derived status from item rows."""
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT status FROM ml_batch_items WHERE batch_id = ?",
            (batch_id,),
        )
        statuses = [str(dict(r).get("status") or "pending") for r in cur.fetchall()]
        cur.execute(
            "SELECT cancel_requested FROM ml_batches WHERE id = ?",
            (batch_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cancel_requested = bool(dict(row).get("cancel_requested"))
        completed = sum(1 for s in statuses if s == "done")
        failed = sum(1 for s in statuses if s == "error")
        cancelled = sum(1 for s in statuses if s in ("cancelled", "skipped"))
        pending = sum(1 for s in statuses if s == "pending")
        running = sum(1 for s in statuses if s == "running")
        status = _derive_batch_status(cancel_requested, pending, running, completed, failed)
        cur.execute(
            """
            UPDATE ml_batches
            SET status = ?, total = ?, completed = ?, failed = ?, cancelled = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (status, len(statuses), completed, failed, cancelled, _now_iso(), batch_id),
        )
        conn.commit()
    except Exception:
        logger.debug("ml_batch refresh failed", exc_info=True)
        return None
    finally:
        conn.close()
    return get_batch(batch_id)


def claim_next_pending_item(batch_id: str) -> dict[str, Any] | None:
    """Mark the oldest pending item running and return it (None when drained).

    Safe under the single event loop — no awaits between SELECT and UPDATE.
    """
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, batch_id, seq, strategy, config_json, validate_after,
                   status, job_id, error, retry_count, last_error,
                   created_at, updated_at
            FROM ml_batch_items
            WHERE batch_id = ? AND status = 'pending'
            ORDER BY seq LIMIT 1
            """,
            (batch_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        item = _item_from_row(dict(row))
        cur.execute(
            "UPDATE ml_batch_items SET status = 'running', error = NULL, updated_at = ? WHERE id = ?",
            (_now_iso(), item["item_id"]),
        )
        conn.commit()
        item["status"] = "running"
        return item
    finally:
        conn.close()


def set_item_status(
    item_id: str,
    status: str,
    *,
    job_id: str | None = None,
    error: str | None = None,
) -> None:
    if status not in ITEM_STATUSES:
        return
    conn = _conn()
    try:
        cur = conn.cursor()
        if job_id is not None:
            cur.execute(
                "UPDATE ml_batch_items SET status = ?, job_id = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, job_id, error, _now_iso(), item_id),
            )
        else:
            cur.execute(
                "UPDATE ml_batch_items SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, error, _now_iso(), item_id),
            )
        conn.commit()
    except Exception:
        logger.debug("ml_batch item status update failed", exc_info=True)
    finally:
        conn.close()


def set_item_status_if_not_terminal(
    item_id: str,
    status: str,
    *,
    error: str | None = None,
) -> None:
    """Like ``set_item_status`` but never overwrites a terminal state.

    Shutdown cancellation races with status-poll reconciliation: a poll may
    have finalized the item while the runner was still parked in the
    executor, and the CancelledError handler must not clobber that outcome.
    """
    if status not in ITEM_STATUSES:
        return
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ml_batch_items SET status = ?, error = ?, updated_at = ? "
            "WHERE id = ? AND status NOT IN ('done', 'error', 'cancelled', 'skipped')",
            (status, error, _now_iso(), item_id),
        )
        conn.commit()
    except Exception:
        logger.debug("ml_batch item guarded status update failed", exc_info=True)
    finally:
        conn.close()


def validate_phase_pending(item: dict, job: dict | None) -> bool:
    """True when the item's linked job is a *train* job but the item has
    ``validate_after`` — the walk-forward phase has not produced a linked
    validate job yet, so a finished train job must not read as a done item.
    Once the executor submits validation it re-links the item to the
    validate job, whose own terminal state then drives reconciliation.
    """
    if not item.get("validate_after"):
        return False
    kind = str((job or {}).get("kind") or "train").lower()
    return kind == "train"


def set_item_job_id(item_id: str, job_id: str | None) -> None:
    """Record the ml_jobs id on a running item so cancel can find it."""
    if not item_id or not job_id:
        return
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ml_batch_items SET job_id = ?, updated_at = ? WHERE id = ?",
            (job_id, _now_iso(), item_id),
        )
        conn.commit()
    except Exception:
        logger.debug("ml_batch item job_id update failed", exc_info=True)
    finally:
        conn.close()


def record_item_attempt_error(item_id: str, retry_count: int, last_error: str | None) -> None:
    """Persist the retry counter + most recent attempt error for observability."""
    if not item_id:
        return
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ml_batch_items SET retry_count = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (max(0, int(retry_count or 0)), last_error, _now_iso(), item_id),
        )
        conn.commit()
    except Exception:
        logger.debug("ml_batch item retry state update failed", exc_info=True)
    finally:
        conn.close()


def skip_pending_items(batch_id: str) -> int:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ml_batch_items SET status = 'skipped', updated_at = ? WHERE batch_id = ? AND status = 'pending'",
            (_now_iso(), batch_id),
        )
        n = cur.rowcount
        conn.commit()
        return int(n or 0)
    except Exception:
        logger.debug("ml_batch skip pending failed", exc_info=True)
        return 0
    finally:
        conn.close()


def is_batch_cancel_requested(batch_id: str) -> bool:
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT cancel_requested FROM ml_batches WHERE id = ?", (batch_id,))
        row = cur.fetchone()
        return bool(row and dict(row).get("cancel_requested"))
    except Exception:
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cancel / retry
# ---------------------------------------------------------------------------


def cancel_batch(
    batch_id: str,
    *,
    cancel_job: Callable[[str], dict] | None = None,
) -> dict[str, Any] | None:
    """Cancel the in-flight job, skip pending items, mark batch cancelled."""
    _ensure_tables()
    batch = get_batch(batch_id)
    if batch is None:
        return None
    if cancel_job is None:
        from app.services.bots.ml_job_store import request_ml_job_cancel

        cancel_job = request_ml_job_cancel
    if batch["status"] in _BATCH_TERMINAL:
        return batch

    now = _now_iso()
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ml_batches SET cancel_requested = 1, status = 'cancelled', updated_at = ? WHERE id = ?",
            (now, batch_id),
        )
        cur.execute(
            "UPDATE ml_batch_items SET status = 'skipped', updated_at = ? WHERE batch_id = ? AND status = 'pending'",
            (now, batch_id),
        )
        cur.execute(
            "SELECT id, job_id FROM ml_batch_items WHERE batch_id = ? AND status = 'running'",
            (batch_id,),
        )
        running_items = [dict(r) for r in cur.fetchall()]
        conn.commit()
    finally:
        conn.close()

    with _runner_lock:
        task = _runner_tasks.get(batch_id)
        live_runner = bool(task is not None and not task.done())

    for item in running_items:
        jid = item.get("job_id")
        cancelled_job = False
        if jid:
            try:
                res = cancel_job(jid)
                cancelled_job = bool(isinstance(res, dict) and res.get("ok"))
            except Exception:
                logger.debug("batch cancel: job cancel failed for %s", jid, exc_info=True)
        if not live_runner and not cancelled_job:
            # No runner left to observe the terminal transition — finalize now.
            set_item_status(item["id"], "cancelled", error="cancelled")
    return _refresh_batch_status(batch_id)


def retry_batch(batch_id: str) -> dict[str, Any] | None:
    """Re-queue items in ``error``/``cancelled``. Returns batch + ``requeued``.

    Manual retry is a fresh user intent — the automatic transient-retry budget
    (``retry_count`` / ``last_error``) resets with the re-queue.
    """
    _ensure_tables()
    batch = get_batch(batch_id)
    if batch is None:
        return None
    out = dict(batch)
    out["requeued"] = 0
    if batch["status"] in ("queued", "running"):
        return out
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE ml_batch_items
            SET status = 'pending', job_id = NULL, error = NULL,
                retry_count = 0, last_error = NULL, updated_at = ?
            WHERE batch_id = ? AND status IN ('error', 'cancelled')
            """,
            (_now_iso(), batch_id),
        )
        requeued = int(cur.rowcount or 0)
        if requeued:
            cur.execute(
                "UPDATE ml_batches SET cancel_requested = 0, status = 'queued', updated_at = ? WHERE id = ?",
                (_now_iso(), batch_id),
            )
        conn.commit()
    finally:
        conn.close()
    refreshed = _refresh_batch_status(batch_id) if requeued else get_batch(batch_id)
    if refreshed is None:
        return None
    refreshed["requeued"] = requeued
    return refreshed


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------


def recover_interrupted_batches() -> list[str]:
    """Mark restart-orphaned items; return batch ids that still have pending work."""
    _ensure_tables()
    conn = _conn()
    affected: list[str] = []
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT id, fail_fast, cancel_requested FROM ml_batches WHERE status IN ('queued', 'running')"
            )
            rows = [dict(r) for r in cur.fetchall()]
        except Exception:
            # Propagate so resume_incomplete_batches can reopen its guard and
            # retry on the next request — a transient DB error at boot must
            # not disable recovery for the rest of the process lifetime.
            logger.warning("ml_batch recovery scan failed", exc_info=True)
            raise
        resumed: list[str] = []
        now = _now_iso()
        for row in rows:
            bid = row.get("id")
            if not bid:
                continue
            affected.append(bid)
            cur.execute(
                "SELECT id, job_id, validate_after, error FROM ml_batch_items WHERE batch_id = ? AND status = 'running'",
                (bid,),
            )
            running_items = [dict(r) for r in cur.fetchall()]
            interrupted = 0
            for item in running_items:
                item_id = item.get("id")
                job_id = item.get("job_id")
                # If the linked job actually finished before the restart, preserve its
                # terminal outcome instead of marking the item as failed.
                if job_id:
                    try:
                        from app.services.bots.ml_job_store import get_ml_job

                        job = get_ml_job(job_id)
                    except Exception:
                        job = None
                    if job and job.get("status") in ("done", "error", "cancelled"):
                        js = job["status"]
                        if js == "done" and validate_phase_pending(item, job):
                            # Train finished but the walk-forward never ran —
                            # not a completed item; retry re-runs it.
                            cur.execute(
                                "UPDATE ml_batch_items SET status = 'error', error = ?, updated_at = ? WHERE id = ?",
                                ("interrupted before validation — retry to re-run", now, item_id),
                            )
                            interrupted += 1
                        elif js == "done":
                            cur.execute(
                                "UPDATE ml_batch_items SET status = 'done', error = NULL, updated_at = ? WHERE id = ?",
                                (now, item_id),
                            )
                        elif js == "cancelled":
                            cur.execute(
                                "UPDATE ml_batch_items SET status = 'cancelled', error = ?, updated_at = ? WHERE id = ?",
                                ("cancelled", now, item_id),
                            )
                        else:
                            cur.execute(
                                "UPDATE ml_batch_items SET status = 'error', error = ?, updated_at = ? WHERE id = ?",
                                (str(job.get("error") or "training failed"), now, item_id),
                            )
                        continue
                cur.execute(
                    "UPDATE ml_batch_items SET status = 'error', error = 'server restarted', updated_at = ? WHERE id = ?",
                    (now, item_id),
                )
                interrupted += 1
            if row.get("cancel_requested"):
                conn.commit()
                continue
            cur.execute(
                "SELECT COUNT(*) AS n FROM ml_batch_items WHERE batch_id = ? AND status = 'pending'",
                (bid,),
            )
            pending = int(dict(cur.fetchone() or {}).get("n") or 0)
            if pending and row.get("fail_fast") and interrupted:
                cur.execute(
                    "UPDATE ml_batch_items SET status = 'skipped', updated_at = ? WHERE batch_id = ? AND status = 'pending'",
                    (now, bid),
                )
                pending = 0
            if pending:
                cur.execute(
                    "UPDATE ml_batches SET status = 'queued', updated_at = ? WHERE id = ?",
                    (now, bid),
                )
                resumed.append(bid)
            conn.commit()
    finally:
        conn.close()
    # Settle terminal state for batches that have no work left; resumed ones
    # stay 'queued' until their runner claims the next item.
    for bid in affected:
        if bid not in resumed:
            _refresh_batch_status(bid)
    return resumed


def resume_incomplete_batches(state: Any = None, *, event_bus: Any = None) -> list[str]:
    """Startup / first-request hook — recover survivors and respawn runners."""
    global _resumed
    with _runner_lock:
        if _resumed:
            return []
        _resumed = True
    try:
        recovered = recover_interrupted_batches()
    except Exception:
        # Reopen the guard so the next request retries instead of leaving
        # recovery disabled for the rest of the process lifetime.
        with _runner_lock:
            _resumed = False
        logger.warning("ML batch recovery failed — will retry on next request", exc_info=True)
        return []
    started = []
    for bid in recovered:
        if start_batch_runner(bid, state, event_bus=event_bus):
            started.append(bid)
    if started:
        logger.info("ML batch runner resumed %d interrupted batch(es)", len(started))
    return started


def ensure_batch_runner(
    batch_id: str,
    state: Any = None,
    *,
    event_bus: Any = None,
) -> bool:
    """Respawn the runner when a non-terminal batch has no live task.

    The runner is an asyncio task; if it dies mid-process (e.g. a cancelled
    pool future propagating ``CancelledError`` past ``_run_batch_guarded``)
    the batch otherwise sits 'queued' forever — ``resume_incomplete_batches``
    is a once-per-process guard and never fires again. The status endpoint
    calls this on every poll so a dead runner self-heals. Returns True when a
    new runner was spawned.
    """
    batch = get_batch(batch_id)
    if batch is None:
        return False
    if batch.get("status") not in ("queued", "running"):
        return False
    if batch.get("cancel_requested"):
        return False
    with _runner_lock:
        task = _runner_tasks.get(batch_id)
        alive = task is not None and not task.done()
    if alive:
        return False
    # A 'running' item with no live runner means the task died mid-item.
    # Reconcile against the job store: terminal jobs finalize the item; a job
    # still in flight becomes a retryable error instead of retraining
    # underneath it (two writers on the same artifact would race).
    changed = False
    for item in batch.get("items") or []:
        if item.get("status") != "running":
            continue
        job = None
        if item.get("job_id"):
            try:
                from app.services.bots.ml_job_store import get_ml_job

                job = get_ml_job(item["job_id"])
            except Exception:
                job = None
        js = str((job or {}).get("status") or "").lower()
        if js == "done" and validate_phase_pending(item, job):
            # Train done but the walk-forward phase never ran and the runner
            # is dead — surface as a retryable error, not a silent pass.
            set_item_status(
                item["item_id"], "error",
                error="interrupted before validation — retry to re-run",
            )
        elif js == "done":
            set_item_status(item["item_id"], "done")
        elif js == "cancelled":
            set_item_status(item["item_id"], "cancelled", error=item.get("error") or "cancelled")
        elif js == "error":
            set_item_status(
                item["item_id"], "error", error=str(job.get("error") or "training failed"),
            )
        else:
            set_item_status(
                item["item_id"], "error", error="batch runner lost — retry to re-run",
            )
        changed = True
    if changed:
        batch = _refresh_batch_status(batch_id) or batch
    if not any((it or {}).get("status") == "pending" for it in (batch.get("items") or [])):
        return False
    # Throttle respawns: if the runner crash-loops (e.g. claim keeps raising
    # on a locked DB), a status poll every few seconds would otherwise spawn
    # a doomed task each time and hammer the store.
    now = time.monotonic()
    with _runner_lock:
        last = _last_spawn_attempt.get(batch_id) or 0.0
        if now - last < RESPAWN_COOLDOWN_SEC:
            return False
        _last_spawn_attempt[batch_id] = now
    started = start_batch_runner(batch_id, state, event_bus=event_bus)
    if started:
        logger.warning("ML batch %s runner respawned after silent stop", batch_id)
    return started


def is_batch_stalled(
    batch: dict | None,
    *,
    now: float | None = None,
    threshold_sec: float = STALL_THRESHOLD_SEC,
) -> bool:
    """True when a live batch has made no visible progress for threshold_sec.

    'queued' with pending items but no running item is normal for the brief
    between-items window; past the threshold it means the runner is gone or
    wedged. Batches with a running item report progress through the job's own
    progress file, not ``updated_at``, so they are never flagged here.
    """
    if not batch:
        return False
    if str(batch.get("status") or "") not in ("queued", "running"):
        return False
    if batch.get("cancel_requested"):
        return False
    items = batch.get("items") or []
    pending = sum(1 for it in items if (it or {}).get("status") == "pending")
    running = sum(1 for it in items if (it or {}).get("status") == "running")
    if not pending or running:
        return False
    try:
        updated = datetime.fromisoformat(
            str(batch.get("updated_at") or "").replace("Z", "+00:00")
        ).timestamp()
    except ValueError:
        return False
    ref = time.time() if now is None else now
    return (ref - updated) >= threshold_sec


def latest_active_batch(symbol: str | None = None) -> dict[str, Any] | None:
    """Most recent non-terminal batch, optionally filtered by symbol.

    Backs ``GET /ml/batch-train/active`` so the UI can re-attach after a
    reload without having persisted a batch_id.
    """
    _ensure_tables()
    conn = _conn()
    try:
        cur = conn.cursor()
        if symbol:
            cur.execute(
                "SELECT id FROM ml_batches WHERE status IN ('queued', 'running') "
                "AND symbol = ? ORDER BY created_at DESC LIMIT 1",
                (str(symbol).upper(),),
            )
        else:
            cur.execute(
                "SELECT id FROM ml_batches WHERE status IN ('queued', 'running') "
                "ORDER BY created_at DESC LIMIT 1",
            )
        row = cur.fetchone()
    except Exception:
        logger.debug("ml_batch latest_active failed", exc_info=True)
        return None
    finally:
        conn.close()
    if not row:
        return None
    return get_batch(str(dict(row).get("id")))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def start_batch_runner(
    batch_id: str,
    state: Any = None,
    *,
    event_bus: Any = None,
    item_executor: ItemExecutor | None = None,
) -> bool:
    """Spawn the background runner task (no-op when one is already alive)."""
    with _runner_lock:
        task = _runner_tasks.get(batch_id)
        if task is not None and not task.done():
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("ML batch runner %s not started — no running event loop", batch_id)
            return False
        task = loop.create_task(
            _run_batch_guarded(batch_id, state, event_bus=event_bus, item_executor=item_executor)
        )
        _runner_tasks[batch_id] = task
        return True


async def _run_batch_guarded(
    batch_id: str,
    state: Any,
    *,
    event_bus: Any = None,
    item_executor: ItemExecutor | None = None,
) -> None:
    try:
        await run_batch(batch_id, state=state, event_bus=event_bus, item_executor=item_executor)
    except asyncio.CancelledError:
        # Cancellation leaves the batch 'queued'/'running' with pending items
        # and no runner — log loudly so the silent-stall pattern is visible,
        # and let ensure_batch_runner / restart recovery pick the batch up.
        logger.warning(
            "ML batch runner %s cancelled mid-flight — batch left resumable", batch_id,
        )
        raise
    except Exception as exc:
        logger.exception("ML batch runner %s crashed", batch_id)
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE ml_batches SET status = 'failed', updated_at = ? WHERE id = ?",
                (_now_iso(), batch_id),
            )
            cur.execute(
                """
                UPDATE ml_batch_items
                SET status = 'error', error = ?, updated_at = ?
                WHERE batch_id = ? AND status = 'running'
                """,
                (f"batch runner crashed: {exc}", _now_iso(), batch_id),
            )
            conn.commit()
        except Exception:
            logger.debug("ml_batch crash persist failed", exc_info=True)
        finally:
            conn.close()
    finally:
        with _runner_lock:
            _runner_tasks.pop(batch_id, None)


async def run_batch(
    batch_id: str,
    *,
    state: Any = None,
    event_bus: Any = None,
    item_executor: ItemExecutor | None = None,
) -> dict[str, Any] | None:
    """Process a batch's pending items, honoring concurrency + fail_fast.

    Workers are asyncio tasks claiming items in ``seq`` order (the create-time
    schedule). ``concurrency`` is capped by ``resolve_ml_train_max_workers()``
    so batch parallelism never exceeds the process pool the executor submits
    to; the default (1) stays strictly serial.
    """
    _ensure_tables()
    batch = get_batch(batch_id)
    if batch is None:
        return None
    if batch["status"] in _BATCH_TERMINAL:
        return batch

    if item_executor is not None:
        executor = item_executor
    else:
        async def executor(b: dict, item: dict) -> dict:
            return await execute_batch_item(state, b, item, event_bus=event_bus)

    try:
        from app.services.bots.ml_train_executor import resolve_ml_train_max_workers

        cap = max(1, int(resolve_ml_train_max_workers()))
    except Exception:
        cap = 1
    workers_n = max(1, min(int(batch.get("concurrency") or 1), cap))

    _refresh_batch_status(batch_id)
    shared: dict[str, Any] = {"stop": False}
    workers = [
        asyncio.create_task(_batch_worker(batch_id, executor, shared))
        for _ in range(workers_n)
    ]
    try:
        await asyncio.gather(*workers)
    except asyncio.CancelledError:
        for w in workers:
            w.cancel()
        raise
    return _refresh_batch_status(batch_id)


async def _batch_worker(batch_id: str, executor: ItemExecutor, shared: dict) -> None:
    while not shared.get("stop"):
        if is_batch_cancel_requested(batch_id):
            break
        item = claim_next_pending_item(batch_id)
        if item is None:
            break
        _refresh_batch_status(batch_id)
        batch = get_batch(batch_id) or {"batch_id": batch_id}
        result = await _execute_item_with_retries(batch_id, batch, item, executor)
        _finalize_item_result(item, result)
        refreshed = _refresh_batch_status(batch_id)
        failed = not result.get("ok") and not result.get("cancelled")
        if failed and (refreshed or {}).get("fail_fast"):
            skip_pending_items(batch_id)
            shared["stop"] = True
            _refresh_batch_status(batch_id)
            break


async def _execute_item_with_retries(
    batch_id: str,
    batch: dict,
    item: dict,
    executor: ItemExecutor,
) -> dict:
    """Run one item; retry transient failures in place with backoff.

    The item stays ``running`` across attempts (crash recovery treats it the
    same as a first attempt) and each attempt persists ``retry_count`` /
    ``last_error``. Backoff sleeps in 1s slices so a batch cancel interrupts
    the wait instead of being noticed after it.
    """
    item_id = item.get("item_id")
    retries = 0
    while True:
        try:
            result = await executor(batch, item)
        except asyncio.CancelledError:
            # A status poll may have finalized the item while the executor was
            # parked — never clobber a terminal outcome at shutdown.
            set_item_status_if_not_terminal(item_id, "cancelled", error="cancelled")
            _refresh_batch_status(batch_id)
            raise
        except Exception as exc:
            logger.exception("ML batch %s item %s raised", batch_id, item_id)
            result = {"ok": False, "error": str(exc)}
        if not isinstance(result, dict):
            result = {"ok": False, "error": "invalid executor result"}
        if result.get("ok") or result.get("cancelled"):
            return result

        error = str(result.get("error") or "training failed")
        if (
            retries >= ITEM_MAX_RETRIES
            or is_batch_cancel_requested(batch_id)
            or not is_transient_batch_error(error, result)
        ):
            if retries:
                record_item_attempt_error(item_id, retries, error)
            return result

        retries += 1
        record_item_attempt_error(item_id, retries, error)
        delay = _retry_backoff_seconds(retries)
        logger.info(
            "ML batch %s item %s transient error — retry %d/%d in %.0fs: %s",
            batch_id, item_id, retries, ITEM_MAX_RETRIES, delay, error,
        )
        if not await _retry_backoff_sleep(batch_id, delay):
            return {
                "ok": False,
                "cancelled": True,
                "error": "cancelled",
                "job_id": result.get("job_id"),
            }


async def _retry_backoff_sleep(batch_id: str, seconds: float) -> bool:
    """Interruptible backoff — returns False when the batch was cancelled."""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while True:
        if is_batch_cancel_requested(batch_id):
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        await asyncio.sleep(min(1.0, remaining))


def _finalize_item_result(item: dict, result: dict) -> None:
    item_id = item.get("item_id")
    job_id = result.get("job_id") or item.get("job_id")
    error = result.get("error")
    if result.get("cancelled"):
        status = "cancelled"
        error = error or "cancelled"
    elif result.get("ok"):
        status, error = "done", None
    else:
        status = "error"
        error = str(error or "training failed")

    # The job store is the source of truth once a job exists — reconcile so an
    # external cancel / failure is mirrored onto the item even if the executor
    # returned a stale payload.
    if job_id:
        try:
            from app.services.bots.ml_job_store import get_ml_job

            job = get_ml_job(job_id)
        except Exception:
            job = None
        if job and job.get("status") in ("done", "error", "cancelled"):
            js = job["status"]
            if js == "done":
                status, error = "done", None
            elif js == "cancelled":
                status = "cancelled"
                error = error or "cancelled"
            else:
                status = "error"
                error = str(job.get("error") or error or "training failed")
    set_item_status(item_id, status, job_id=job_id, error=error)


def reconcile_batch_items(batch_id: str) -> dict[str, Any] | None:
    """Poll job-store status for running items (GET self-healing)."""
    batch = get_batch(batch_id)
    if batch is None:
        return None
    dirty = False
    for item in batch.get("items") or []:
        if item.get("status") != "running" or not item.get("job_id"):
            continue
        try:
            from app.services.bots.ml_job_store import get_ml_job

            job = get_ml_job(item["job_id"])
        except Exception:
            job = None
        if not job or job.get("status") not in ("done", "error", "cancelled"):
            continue
        js = job["status"]
        if js == "done":
            if validate_phase_pending(item, job):
                # Train job done but the walk-forward phase is still ahead of
                # the live runner — the item is not complete yet.
                continue
            set_item_status(item["item_id"], "done")
        elif js == "cancelled":
            set_item_status(item["item_id"], "cancelled", error=item.get("error") or "cancelled")
        else:
            # If the item still has retry budget, the runner may be between
            # attempts. Leave it marked running so the UI and counters don't
            # show a premature failure.
            retry_count = int(item.get("retry_count") or 0)
            if retry_count < ITEM_MAX_RETRIES:
                continue
            set_item_status(
                item["item_id"],
                "error",
                error=str(job.get("error") or item.get("error") or "training failed"),
            )
        dirty = True
    if dirty:
        return _refresh_batch_status(batch_id)
    return batch


# ---------------------------------------------------------------------------
# Default item executor — reuses the Lab train / validate submission path
# ---------------------------------------------------------------------------

# Legacy clients may post the Lab Advanced knob snapshot with camelCase keys
# (totalTimesteps, gbmMaxIter, nFolds…). Trainers and the validate_after path
# read snake_case only — map the known keys so user settings are never
# silently dropped. Snake_case always wins when both are present.
_CAMEL_TO_SNAKE_ITEM_CONFIG = {
    "totalTimesteps": "total_timesteps",
    "hiddenDim": "hidden_dim",
    "earlyStopPatience": "early_stop_patience",
    "gbmMaxIter": "gbm_max_iter",
    "gbmMaxDepth": "gbm_max_depth",
    "nFolds": "validate_folds",
    "validateMaxBars": "validate_max_bars",
    "pboSegments": "pbo_segments",
    "pboMaxCombos": "pbo_max_combos",
}


def _normalize_item_config_keys(cfg: dict) -> dict:
    out = dict(cfg)
    for camel, snake in _CAMEL_TO_SNAKE_ITEM_CONFIG.items():
        if camel not in out:
            continue
        if snake not in out:
            out[snake] = out[camel]
        del out[camel]
    return out


async def execute_batch_item(
    state: Any,
    batch: dict,
    item: dict,
    *,
    event_bus: Any = None,
) -> dict[str, Any]:
    """Fetch candles, submit a train job, then optionally validate.

    Mirrors ``ml_train_handler`` config preparation so a batch item trains the
    same champion a manual Lab Train would.
    """
    if state is None:
        return {"ok": False, "error": "batch runner has no app state"}
    if is_batch_cancel_requested(batch.get("batch_id")):
        return {"ok": False, "cancelled": True, "error": "cancelled"}

    from app.api.http.app import _enrich_training_candles, _fetch_training_candles
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe
    from app.services.bots.ml_train_executor import submit_train_job
    from app.services.bots.ml_training_window import (
        bar_limit_for_training_window,
        parse_training_window_months,
        prepare_lab_champion_train_config,
        summarize_training_window,
    )

    strategy = str(item.get("strategy") or "").upper()
    symbol = str(batch.get("symbol") or "").upper()
    cfg = prepare_lab_champion_train_config(
        _normalize_item_config_keys(dict(item.get("config") or {}))
    )
    try:
        from app.services.bots.optimization_store import merge_optimized_train_hyperparams

        cfg = merge_optimized_train_hyperparams(cfg, symbol, strategy, require_opt_in=True)
        cfg = prepare_lab_champion_train_config(cfg)
    except Exception:
        logger.debug("Batch hyperparam merge skipped", exc_info=True)

    tf = normalize_model_timeframe(cfg.get("timeframe"))
    win_months = parse_training_window_months(cfg)
    bar_limit = bar_limit_for_training_window(win_months, timeframe=tf, purpose="train")
    cfg = {**cfg, "timeframe": tf, "training_window_months": win_months}
    try:
        from app.services.bots.ml_data_calendar import calendar_holdout_enabled

        if calendar_holdout_enabled(cfg):
            cfg.setdefault("skip_refit", True)
            cfg["ml_calendar_holdout"] = True
    except Exception:
        pass

    candles = await _fetch_training_candles(
        state, symbol, tf=tf, months=win_months, limit=bar_limit, config=cfg,
    )
    if len(candles) < 200:
        return {"ok": False, "error": f"insufficient candles ({len(candles)})"}
    candles = await asyncio.to_thread(_enrich_training_candles, symbol, candles, strategy, cfg)
    cfg["_training_window"] = summarize_training_window(
        candles,
        win_months,
        bar_limit=bar_limit,
        timeframe=tf,
        calendar=cfg.get("_data_calendar") if isinstance(cfg.get("_data_calendar"), dict) else None,
    )

    if is_batch_cancel_requested(batch.get("batch_id")):
        return {"ok": False, "cancelled": True, "error": "cancelled"}

    job_id = str(uuid.uuid4())
    set_item_job_id(item["item_id"], job_id)
    result = await submit_train_job(
        strategy, symbol, candles, cfg, job_id=job_id, event_bus=event_bus,
    )
    out = dict(result) if isinstance(result, dict) else {"ok": False, "error": "invalid result"}
    out["job_id"] = job_id
    if not out.get("ok") or out.get("cancelled"):
        return out

    if item.get("validate_after"):
        # Re-link the item to the validate job before submitting it: status
        # reconciliation and crash recovery key off the item's job_id, and a
        # done train job must not mask a walk-forward that is still running
        # (or one that failed) — the validate job's own state drives those
        # paths from here on.
        validate_job_id = str(uuid.uuid4())
        set_item_job_id(item["item_id"], validate_job_id)
        vres = await _validate_trained_item(
            state, strategy, symbol, cfg, event_bus=event_bus, job_id=validate_job_id,
        )
        if not isinstance(vres, dict):
            vres = {"ok": False, "error": "invalid validate result"}
        if vres.get("cancelled"):
            return {"ok": False, "cancelled": True, "error": "cancelled", "job_id": validate_job_id}
        if not vres.get("ok"):
            return {
                "ok": False,
                "job_id": validate_job_id,
                "error": f"validate_after failed: {vres.get('error') or 'validation failed'}",
            }
        out["validation"] = {
            "ok": True,
            "mean_accuracy": vres.get("mean_accuracy"),
            "aggregate": vres.get("aggregate"),
        }
    return out


async def _validate_trained_item(
    state: Any,
    strategy: str,
    symbol: str,
    cfg: dict,
    *,
    event_bus: Any = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Walk-forward validate a freshly trained item (same path as Lab Validate)."""
    from app.api.http.app import _fetch_validate_candles_enough
    from app.services.bots.ml_train_executor import submit_validate_job
    from app.services.bots.ml_training_window import (
        bar_limit_for_training_window,
        summarize_training_window,
        validate_min_candles,
    )

    tf = cfg.get("timeframe")
    win_months = int(cfg.get("training_window_months") or 3)
    try:
        n_folds = max(1, min(10, int(cfg.get("validate_folds") or 5)))
    except (TypeError, ValueError):
        n_folds = 5
    mode = str(cfg.get("validate_mode") or "rolling").lower()
    run_pbo = bool(cfg.get("validate_pbo", False))
    try:
        pbo_segments = max(2, min(12, int(cfg.get("pbo_segments") or 6)))
    except (TypeError, ValueError):
        pbo_segments = 6
    wf_parity = bool(cfg.get("wf_capacity_parity", True))
    vcfg = {**cfg, "wf_capacity_parity": wf_parity}

    bar_limit = bar_limit_for_training_window(
        win_months, timeframe=tf, purpose="validate", capacity_parity=wf_parity,
    )
    try:
        user_vmax = int(vcfg.get("validate_max_bars") or 0)
    except (TypeError, ValueError):
        user_vmax = 0
    vmax = min(user_vmax, bar_limit) if user_vmax > 0 else bar_limit
    vcfg["validate_max_bars"] = vmax

    candles, used_months, used_vmax = await _fetch_validate_candles_enough(
        state,
        symbol,
        strategy=strategy,
        tf=tf,
        months=win_months,
        limit=vmax,
        config=vcfg,
        n_folds=n_folds,
    )
    min_needed = validate_min_candles(tf, n_folds=n_folds)
    if len(candles) < min_needed:
        return {
            "ok": False,
            "error": (
                f"Need >= {min_needed} candles for {tf} validation "
                f"(got {len(candles)} after expanding to {used_months}mo)"
            ),
        }
    vcfg["_training_window"] = summarize_training_window(
        candles,
        used_months,
        bar_limit=used_vmax,
        timeframe=tf,
        calendar=vcfg.get("_data_calendar") if isinstance(vcfg.get("_data_calendar"), dict) else None,
    )
    for row in candles:
        if isinstance(row, dict) and not row.get("_symbol"):
            row["_symbol"] = symbol
    return await submit_validate_job(
        strategy,
        symbol,
        candles,
        vcfg,
        n_folds=n_folds,
        mode=mode,
        run_pbo=run_pbo,
        pbo_segments=pbo_segments,
        job_id=job_id,
        event_bus=event_bus,
    )
