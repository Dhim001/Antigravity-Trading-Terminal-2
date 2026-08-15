"""Backtest performance helpers — parallelism caps, tier routing, and estimates."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable, TypeVar

from app.config import (
    BACKTEST_DEFER_HEAVY,
    BACKTEST_INLINE_MAX_SEC,
    BACKTEST_PARALLEL_BACKEND,
    BACKTEST_PARALLEL_MAX,
    BACKTEST_PARALLEL_WORKERS,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


def parallel_worker_count(task_count: int) -> int:
    """Bounded worker count for embarrassingly parallel backtest tasks."""
    tasks = max(0, int(task_count or 0))
    if tasks <= 1:
        return 1
    hard_cap = max(1, min(int(BACKTEST_PARALLEL_MAX), 32))
    cap = max(1, min(int(BACKTEST_PARALLEL_WORKERS), hard_cap))
    return min(cap, tasks)


def parallel_backend() -> str:
    """``thread``, ``process``, or ``auto`` → process when CUDA not in-process.

    Prefer **process** for GIL-bound ML feature/sweep work (saturates cores).
    Prefer **threads** when CUDA EP is already loaded (spawn + CUDA is fragile).
    ``BACKTEST_PARALLEL_BACKEND=auto`` (default) picks process unless CUDA EP
    is available *and* may already be in use; force with ``process`` / ``thread``.
    """
    raw = (BACKTEST_PARALLEL_BACKEND or "auto").strip().lower()
    if raw in ("process", "proc", "multiprocessing"):
        return "process"
    if raw in ("thread", "threads", "threading"):
        return "thread"
    # auto — prefer ProcessPool unless this process already loaded CUDA ORT
    # (spawn + live CUDA context is fragile). Merely having onnxruntime-gpu
    # installed must not force threads for CPU/sklearn sweeps.
    try:
        from app.services.bots.ml_onnx_runtime import cuda_session_loaded_in_process

        if cuda_session_loaded_in_process():
            return "thread"
    except Exception:
        pass
    return "process"


def configure_parallel_thread_env(workers: int) -> dict[str, str]:
    """Suggest ORT/OMP intra-op threads so workers × intra ≈ cores.

    Sets env only when the knobs are currently unset. Returns the applied values.
    """
    cores = max(1, os.cpu_count() or 4)
    w = max(1, int(workers or 1))
    intra = max(1, cores // w)
    applied: dict[str, str] = {}
    for key in ("ORT_INTRA_OP_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        if not (os.environ.get(key) or "").strip():
            os.environ[key] = str(intra)
            applied[key] = str(intra)
    if not (os.environ.get("ORT_INTER_OP_THREADS") or "").strip():
        os.environ["ORT_INTER_OP_THREADS"] = "1"
        applied["ORT_INTER_OP_THREADS"] = "1"
    if applied:
        logger.info(
            "Backtest parallel thread env (workers=%s, cores=%s): %s",
            w, cores, applied,
        )
    return applied


def map_parallel(
    fn: Callable[[T], R],
    items: Iterable[T],
    *,
    workers: int | None = None,
    thread_name_prefix: str = "bt-parallel",
) -> list[R]:
    """Run ``fn`` over ``items`` with ThreadPool or ProcessPool per config.

    Preserves input order. On ProcessPool failure, retries with threads.
    ``fn`` and each item must be picklable when ``BACKTEST_PARALLEL_BACKEND=process``.
    """
    seq = list(items)
    n = len(seq)
    if n == 0:
        return []
    w = parallel_worker_count(n) if workers is None else max(1, min(int(workers), n))
    if w <= 1:
        return [fn(x) for x in seq]

    backend = parallel_backend()
    if backend == "process":
        configure_parallel_thread_env(w)
        try:
            with ProcessPoolExecutor(max_workers=w) as pool:
                return list(pool.map(fn, seq))
        except Exception as exc:
            logger.warning(
                "ProcessPool backtest parallel failed (%s) — falling back to threads",
                exc,
            )

    results: list[R | None] = [None] * n
    with ThreadPoolExecutor(max_workers=w, thread_name_prefix=thread_name_prefix) as pool:
        futures = {pool.submit(fn, item): idx for idx, item in enumerate(seq)}
        for fut in as_completed(futures):
            idx = futures[fut]
            results[idx] = fut.result()
    return results  # type: ignore[return-value]


def _sweep_combo_count(sweep: dict | list | None) -> int:
    if not sweep:
        return 1
    if isinstance(sweep, dict):
        from app.services.bots.backtest_trial_budget import resolve_max_trials

        mode = str(sweep.get("sweep_mode") or "grid").lower()
        if mode in ("random", "lhs", "bayesian"):
            return resolve_max_trials(sweep, mode)
        total = 1
        for vals in sweep.values():
            if isinstance(vals, list) and vals:
                total *= len(vals)
        return max(1, min(total, resolve_max_trials(sweep, "grid")))
    if isinstance(sweep, list):
        return max(1, len(sweep))
    return 1


def estimate_backtest_seconds(
    *,
    days: int = 7,
    sweep: dict | list | None = None,
    walk_forward: bool = False,
    reasoning: bool = False,
    portfolio_symbols: list | None = None,
    meta_label_walk_forward: bool = False,
    rolling_folds: int = 1,
    strategy: str = "",
) -> float:
    """Rough server-side duration estimate for tier routing."""
    parsed_days = max(1, int(days or 7))
    symbol_count = len(portfolio_symbols) if portfolio_symbols else 1
    combos = _sweep_combo_count(sweep)
    folds = max(1, int(rolling_folds or 1))
    strat = str(strategy or "").upper()

    sec = 4.0 + parsed_days * 0.9
    if symbol_count > 1:
        sec *= symbol_count * 0.75
    if combos > 1:
        sec *= min(combos, 24) * 0.45
    if walk_forward and sweep:
        sec *= folds * 1.6
    elif walk_forward:
        sec *= 1.4
    if reasoning:
        sec *= 2.5
    if meta_label_walk_forward:
        sec *= folds * 2.2
    if parsed_days >= 30:
        sec *= 1.35

    # Deep / RL strategies evaluate far slower per bar than TA (live_parity, drift,
    # per-bar inference). Batch inference helps ONNX/sklearn; feature build still
    # bounds throughput. Keep estimates conservative for tier routing.
    # Measured (pre-batch): RL ≈ 20–32 bars/sec, ML_SIGNAL_BOOST ≈ 18–48.
    # With batch: expect ~2–5× on inference-heavy stretches; use mid-conservative bps.
    approx_bars = float(parsed_days) * 1440.0  # 1m bars
    batch_on = os.environ.get("BACKTEST_BATCH_INFERENCE", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )
    vec_on = os.environ.get("BACKTEST_VECTORIZED_FEATURES", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if strat == "RL_PPO_AGENT":
        # Market features precomputed once; ONNX + position splice stay per-bar.
        bps = 80.0 if vec_on else 20.0
        sec = max(sec * (4.0 if vec_on else 12.0), approx_bars / bps)
    elif any(tok in strat for tok in ("LSTM", "TCN", "TRANSFORMER", "VAE", "GNN")):
        # Keep estimates conservative for tier routing even when vectorized+batch
        # speed up research runs (observed pre-opt ~20 bars/s on 1m deep models).
        bps = 50.0 if (batch_on and vec_on) else (45.0 if batch_on else 20.0)
        sec = max(sec * (2.8 if (batch_on and vec_on) else (3.0 if batch_on else 4.0)), approx_bars / bps)
    elif strat.startswith("ML_"):
        # Vectorized features help, but sim/gates still bound wall time — stay
        # below ~43 bars/s so long 1m ML continues to defer (not look "1 min").
        bps = 42.0 if (batch_on and vec_on) else (40.0 if batch_on else 25.0)
        sec = max(sec * (1.5 if (batch_on and vec_on) else (1.6 if batch_on else 2.0)), approx_bars / bps)

    return round(sec, 1)


def is_heavy_backtest(
    *,
    days: int = 7,
    sweep: dict | list | None = None,
    walk_forward: bool = False,
    reasoning: bool = False,
    portfolio_symbols: list | None = None,
    meta_label_walk_forward: bool = False,
    strategy: str = "",
) -> bool:
    """True when the run should execute in a background task (not block the WS handler)."""
    from app.config import BACKTEST_FORCE_DEFER_OPTIMIZATION

    strat = str(strategy or "").upper()
    if strat == "RL_PPO_AGENT":
        return True
    if any(tok in strat for tok in ("LSTM", "TCN", "TRANSFORMER")) and int(days or 7) >= 14:
        return True
    if BACKTEST_FORCE_DEFER_OPTIMIZATION and (sweep or walk_forward or meta_label_walk_forward):
        return True
    if not BACKTEST_DEFER_HEAVY:
        return False
    if portfolio_symbols and len(portfolio_symbols) > 1:
        return True
    if reasoning:
        return True
    if walk_forward:
        return True
    if sweep:
        return True
    if meta_label_walk_forward:
        return True
    if int(days or 7) >= 30:
        return True
    return False


def classify_backtest_tier(req: dict[str, Any] | None) -> str:
    """Fast (<30s) inline; slow portfolio/WF/reasoning deferred to job queue."""
    req = req or {}
    config = req.get("config") or {}
    days = int(req.get("days") or 7)
    sweep = req.get("sweep")
    walk_forward = bool(req.get("walk_forward"))
    reasoning = bool(req.get("reasoning"))
    portfolio_symbols = req.get("portfolio_symbols")
    meta_label_wf = bool(config.get("meta_label_walk_forward"))
    rolling_folds = int(req.get("rolling_folds") or 1)
    strategy = str(req.get("strategy") or "")

    if is_heavy_backtest(
        days=days,
        sweep=sweep,
        walk_forward=walk_forward,
        reasoning=reasoning,
        portfolio_symbols=portfolio_symbols,
        meta_label_walk_forward=meta_label_wf,
        strategy=strategy,
    ):
        return "deferred"

    est = estimate_backtest_seconds(
        days=days,
        sweep=sweep,
        walk_forward=walk_forward,
        reasoning=reasoning,
        portfolio_symbols=portfolio_symbols,
        meta_label_walk_forward=meta_label_wf,
        rolling_folds=rolling_folds,
        strategy=strategy,
    )
    if est > float(BACKTEST_INLINE_MAX_SEC):
        return "deferred"
    return "inline"


def heavy_backtest_label(req: dict[str, Any]) -> str:
    if req.get("portfolio_symbols") and len(req["portfolio_symbols"]) > 1:
        return "portfolio"
    if req.get("reasoning"):
        return "reasoning"
    if req.get("walk_forward") and req.get("sweep"):
        return "walk-forward"
    if req.get("sweep"):
        return "sweep"
    cfg = req.get("config") or {}
    if cfg.get("meta_label_walk_forward"):
        return "meta-label-wf"
    return "long-range"


def backtest_tier_meta(req: dict[str, Any] | None) -> dict[str, Any]:
    """Metadata attached to jobs and run manifests."""
    req = req or {}
    config = req.get("config") or {}
    tier = classify_backtest_tier(req)
    est = estimate_backtest_seconds(
        days=int(req.get("days") or 7),
        sweep=req.get("sweep"),
        walk_forward=bool(req.get("walk_forward")),
        reasoning=bool(req.get("reasoning")),
        portfolio_symbols=req.get("portfolio_symbols"),
        meta_label_walk_forward=bool(config.get("meta_label_walk_forward")),
        rolling_folds=int(req.get("rolling_folds") or 1),
        strategy=str(req.get("strategy") or ""),
    )
    return {
        "tier": tier,
        "estimated_sec": est,
        "label": heavy_backtest_label({**req, "config": config}) if tier == "deferred" else "baseline",
    }
