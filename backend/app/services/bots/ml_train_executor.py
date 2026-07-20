"""Process-isolated ML train/validate (MEMORY_CENTRIC_REVIEW #9).

Torch / ONNX training peaks in a worker process so the live feed/OMS RSS
stays flat. max_workers=1 queues concurrent train/validate requests.

Also registers jobs in ``ml_job_store`` (ML Lab Phase 1) with optional
progress-file polling + WS broadcast.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ProcessPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)

_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()


def _max_workers() -> int:
    from app.config import ML_TRAIN_MAX_WORKERS
    return max(1, int(ML_TRAIN_MAX_WORKERS))


def get_ml_train_pool() -> ProcessPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            from app.services.bots.ml_train_limits import apply_ml_train_rss_limit

            _pool = ProcessPoolExecutor(
                max_workers=_max_workers(),
                initializer=apply_ml_train_rss_limit,
            )
        return _pool


def shutdown_ml_train_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None


def run_train_job(strategy: str, symbol: str, candles: list, config: dict | None) -> dict[str, Any]:
    """Picklable top-level entry — runs inside the worker process."""
    from app.services.bots.ml_job_progress import (
        ml_cancel_requested,
        progress_path_from_config,
        write_ml_progress,
    )

    strat = str(strategy or "").upper()
    cfg = dict(config or {})
    progress_path = progress_path_from_config(cfg)

    if ml_cancel_requested(progress_path):
        return {"ok": False, "cancelled": True, "error": "cancelled"}

    write_ml_progress(progress_path, pct=1, phase="start", detail=strat)

    trainers = {
        "ML_SIGNAL_BOOST": ("app.services.bots.strategies_ml", "train_ml_signal_model"),
        "LSTM_DIRECTION": ("app.services.bots.ml_lstm_trainer", "train_lstm_signal_model"),
        "RL_PPO_AGENT": ("app.services.bots.rl_ppo_trainer", "train_ppo_agent"),
        "TCN_MULTI_HORIZON": ("app.services.bots.ml_tcn_trainer", "train_tcn_model"),
        "VAE_REGIME_DETECTOR": ("app.services.bots.ml_vae_regime", "train_vae_regime_model"),
        "TRANSFORMER_SIGNAL": ("app.services.bots.ml_transformer_trainer", "train_transformer_model"),
        "GNN_CROSS_ASSET": ("app.services.bots.ml_gnn_trainer", "train_gnn_model"),
    }
    entry = trainers.get(strat)
    if not entry:
        return {"ok": False, "error": f"training not supported for {strat}"}
    mod_name, fn_name = entry
    import importlib
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, fn_name)
    result = fn(symbol, candles, config=cfg)
    if isinstance(result, dict) and result.get("cancelled"):
        write_ml_progress(progress_path, pct=100, phase="cancelled", detail="cancelled")
        return result
    write_ml_progress(progress_path, pct=100, phase="done", detail="complete")
    return result


def run_validate_job(
    strategy: str,
    symbol: str,
    candles: list,
    config: dict | None,
    n_folds: int,
    mode: str,
    run_pbo: bool,
    pbo_segments: int,
) -> dict[str, Any]:
    """Picklable WF (+ optional PBO) entry for the worker process."""
    from app.services.bots.ml_job_progress import (
        ml_cancel_requested,
        progress_path_from_config,
        write_ml_progress,
    )
    from app.services.bots.ml_walk_forward_validator import walk_forward_ml_train

    cfg = dict(config or {})
    progress_path = progress_path_from_config(cfg)
    if ml_cancel_requested(progress_path):
        return {"ok": False, "cancelled": True, "error": "cancelled"}

    write_ml_progress(progress_path, pct=2, phase="validate", detail="walk-forward")

    cfg.setdefault("symbol", symbol)
    cfg.setdefault("model_symbol", symbol)
    cfg["_wf_mode"] = True
    cfg.setdefault("skip_refit", True)
    cfg.setdefault("skip_snapshot", True)
    cfg.setdefault("max_iter", 40)

    strat_u = str(strategy or "").upper()
    if strat_u == "RL_PPO_AGENT":
        cfg.setdefault("total_timesteps", 2048)
        cfg.setdefault("n_steps", 512)
        cfg.setdefault("ppo_epochs", 2)
        cfg.setdefault("hidden_dim", 64)
        cfg.setdefault("validate_max_bars", 1200)
        if run_pbo and not bool(cfg.get("force_pbo")):
            run_pbo = False
            cfg["_pbo_skipped"] = "rl_too_expensive"

    max_bars = int(cfg.get("validate_max_bars", 2500))
    if len(candles) > max_bars:
        candles = candles[-max_bars:]

    wf_result = walk_forward_ml_train(
        strategy, symbol, candles,
        config=cfg, n_folds=n_folds, mode=mode,
    )
    result = dict(wf_result)
    if result.get("cancelled"):
        write_ml_progress(progress_path, pct=100, phase="cancelled", detail="cancelled")
        return result

    agg = result.get("aggregate") if isinstance(result.get("aggregate"), dict) else {}
    if result.get("ok") and agg.get("mean_oos_accuracy") is not None:
        result.setdefault("mean_accuracy", agg.get("mean_oos_accuracy"))

    if cfg.get("_pbo_skipped"):
        result["pbo"] = {
            "ok": False,
            "skipped": True,
            "error": (
                "PBO skipped for RL_PPO_AGENT (too slow for interactive validate). "
                "Set config.force_pbo=true to run anyway."
            ),
        }

    if run_pbo and wf_result.get("ok"):
        write_ml_progress(progress_path, pct=90, phase="pbo", detail="computing PBO")
        if ml_cancel_requested(progress_path):
            result["ok"] = False
            result["cancelled"] = True
            result["error"] = "cancelled"
            return result
        try:
            from app.services.bots.ml_pbo_validator import compute_ml_pbo
            result["pbo"] = compute_ml_pbo(
                strategy, symbol, candles,
                config=cfg,
                n_segments=min(pbo_segments, 4),
                max_combos=min(4, int(cfg.get("pbo_max_combos", 4))),
            )
        except Exception as exc:
            logger.exception("PBO failed for %s/%s", strategy, symbol)
            result["pbo"] = {"ok": False, "error": str(exc)}

    if result.get("ok"):
        try:
            from app.services.bots.ml_model_artifacts import persist_ml_validation_metadata

            persist_res = persist_ml_validation_metadata(
                strategy,
                symbol,
                result,
                pbo_result=result.get("pbo") if isinstance(result.get("pbo"), dict) else None,
            )
            result["validation_persisted"] = persist_res
        except Exception as exc:
            logger.exception("Failed to persist ML validation metadata for %s/%s", strategy, symbol)
            result["validation_persisted"] = {"ok": False, "error": str(exc)}

    write_ml_progress(progress_path, pct=100, phase="done", detail="complete")
    return result


async def _publish_ml_progress(event_bus: Any, job_id: str, progress: dict) -> None:
    if event_bus is None:
        return
    try:
        from app.api.outbound import ml_job_progress
        from app.services.events import channels

        payload = ml_job_progress({
            "job_id": job_id,
            **(progress or {}),
        })
        await event_bus.publish(channels.WS_BROADCAST, payload)
    except Exception:
        logger.debug("ml_job_progress publish failed", exc_info=True)


async def _poll_progress_loop(
    job_id: str,
    progress_path: str,
    stop: asyncio.Event,
    event_bus: Any = None,
) -> None:
    from app.services.bots.ml_job_progress import read_ml_progress
    from app.services.bots.ml_job_store import update_ml_job_progress

    last: tuple | None = None
    while not stop.is_set():
        data = read_ml_progress(progress_path)
        if data:
            key = (data.get("pct"), data.get("phase"), data.get("detail"))
            if key != last:
                last = key
                job = update_ml_job_progress(job_id, data)
                if job:
                    await _publish_ml_progress(event_bus, job_id, {
                        "pct": data.get("pct"),
                        "phase": data.get("phase"),
                        "detail": data.get("detail"),
                        "kind": job.get("kind"),
                        "strategy": job.get("strategy"),
                        "symbol": job.get("symbol"),
                        "status": job.get("status"),
                    })
        try:
            await asyncio.wait_for(stop.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass


def _prepare_job_config(
    kind: str,
    strategy: str,
    symbol: str,
    config: dict | None,
    *,
    job_id: str | None = None,
) -> tuple[str, dict, str]:
    """Create/register job + inject progress path into config. Returns (job_id, cfg, path)."""
    from app.services.bots.ml_job_progress import make_progress_path
    from app.services.bots.ml_job_store import (
        create_ml_job,
        get_ml_job,
        set_ml_job_progress_path,
    )

    cfg = dict(config or {})
    if job_id:
        jid = job_id
        existing = get_ml_job(jid)
        if not existing:
            path = make_progress_path(jid)
            create_ml_job(
                kind=kind,
                strategy=strategy,
                symbol=symbol,
                progress_path=path,
                job_id=jid,
            )
        else:
            path = existing.get("progress_path") or make_progress_path(jid)
            set_ml_job_progress_path(jid, path)
    else:
        path = make_progress_path(f"{kind}_{symbol}")
        jid = create_ml_job(
            kind=kind,
            strategy=strategy,
            symbol=symbol,
            progress_path=path,
        )
    cfg["_progress_path"] = path
    cfg["_ml_job_id"] = jid
    return jid, cfg, path


def _finalize_job(job_id: str, result: dict[str, Any]) -> dict[str, Any]:
    from app.services.bots.ml_job_store import finish_ml_job, is_ml_job_cancelled

    out = dict(result) if isinstance(result, dict) else {"ok": False, "error": "invalid result"}
    out.setdefault("job_id", job_id)

    if is_ml_job_cancelled(job_id) or out.get("cancelled"):
        out["cancelled"] = True
        out["ok"] = False
        out.setdefault("error", "cancelled")
        finish_ml_job(job_id, "cancelled", result=out, error="cancelled")
        return out

    if out.get("ok"):
        finish_ml_job(job_id, "done", result=out)
    else:
        finish_ml_job(job_id, "error", result=out, error=str(out.get("error") or "failed"))
    return out


async def _run_in_pool(fn, *args, job_id: str | None = None):
    """Submit to process pool (or thread fallback); track Future for cancel."""
    from app.config import ML_TRAIN_PROCESS_ISOLATION
    from app.services.bots.ml_job_store import (
        attach_ml_job_future,
        is_ml_job_cancelled,
        mark_ml_job_running,
    )

    # Never start work the user already cancelled (queued cancel race).
    if job_id and is_ml_job_cancelled(job_id):
        return {"ok": False, "cancelled": True, "error": "cancelled"}

    if ML_TRAIN_PROCESS_ISOLATION:
        try:
            pool = get_ml_train_pool()
            if job_id and is_ml_job_cancelled(job_id):
                return {"ok": False, "cancelled": True, "error": "cancelled"}
            cfut = pool.submit(fn, *args)
            if job_id:
                attach_ml_job_future(job_id, cfut)
                # Future may still be cancelled before the worker starts.
                if is_ml_job_cancelled(job_id) and not cfut.running() and not cfut.done():
                    cfut.cancel()
                    return {"ok": False, "cancelled": True, "error": "cancelled"}
            # Flip to running only once the future is accepted (may still wait in pool).
            # Progress-file first write also promotes queued→running.
            mark_ml_job_running(job_id)
            return await asyncio.wrap_future(cfut)
        except Exception as exc:
            # Do not pull heavy torch/ONNX work into the live feed process after a
            # BrokenProcessPool / RSS kill — fail the job instead (MEMORY #9/#27).
            logger.error("ML process pool failed — not falling back to in-process thread: %s", exc)
            raise

    mark_ml_job_running(job_id)
    # Isolation off — cooperative cancel still via progress file.
    return await asyncio.to_thread(fn, *args)


async def submit_train_job(
    strategy: str,
    symbol: str,
    candles: list,
    config: dict | None,
    *,
    job_id: str | None = None,
    event_bus: Any = None,
) -> dict[str, Any]:
    """Run train in process pool (or thread fallback) and invalidate parent caches."""
    from app.services.bots.ml_job_progress import cleanup_ml_progress
    from app.services.bots.ml_model_artifacts import invalidate_strategy_model_caches

    jid, cfg, progress_path = _prepare_job_config(
        "train", strategy, symbol, config, job_id=job_id,
    )
    from app.services.bots.ml_job_store import is_ml_job_cancelled

    if is_ml_job_cancelled(jid):
        cleanup_ml_progress(progress_path)
        return _finalize_job(jid, {"ok": False, "cancelled": True, "error": "cancelled"})

    stop = asyncio.Event()
    poll_task = asyncio.create_task(
        _poll_progress_loop(jid, progress_path, stop, event_bus=event_bus),
    )

    out: dict[str, Any] = {"ok": False, "error": "train did not complete"}
    try:
        result = await _run_in_pool(
            run_train_job, strategy, symbol, candles, cfg, job_id=jid,
        )
        out = _finalize_job(jid, result if isinstance(result, dict) else {"ok": False, "error": "invalid train result"})
    except asyncio.CancelledError:
        from app.services.bots.ml_job_store import finish_ml_job, request_ml_job_cancel
        request_ml_job_cancel(jid)
        finish_ml_job(jid, "cancelled", error="cancelled")
        raise
    except Exception as exc:
        from app.services.bots.ml_job_store import finish_ml_job
        logger.exception("ML train job %s failed", jid)
        finish_ml_job(jid, "error", error=str(exc))
        out = {"ok": False, "error": str(exc), "job_id": jid}
        raise
    finally:
        stop.set()
        try:
            await poll_task
        except Exception:
            pass
        cleanup_ml_progress(progress_path)

    # Only refresh in-memory caches when a successful train actually wrote artifacts.
    if isinstance(out, dict) and out.get("ok") and not out.get("cancelled"):
        try:
            invalidate_strategy_model_caches(strategy, symbol)
        except Exception:
            logger.exception("Parent model cache invalidate failed after train %s/%s", strategy, symbol)
        try:
            from app.services.bots.ml_retrain_scheduler import get_retrain_scheduler

            get_retrain_scheduler().record_retrain(strategy, symbol)
        except Exception:
            logger.exception("record_retrain failed after train %s/%s", strategy, symbol)

    return out


async def submit_validate_job(
    strategy: str,
    symbol: str,
    candles: list,
    config: dict | None,
    *,
    n_folds: int,
    mode: str,
    run_pbo: bool,
    pbo_segments: int,
    job_id: str | None = None,
    event_bus: Any = None,
) -> dict[str, Any]:
    from app.services.bots.ml_job_progress import cleanup_ml_progress
    from app.services.bots.ml_model_artifacts import invalidate_strategy_model_caches

    jid, cfg, progress_path = _prepare_job_config(
        "validate", strategy, symbol, config, job_id=job_id,
    )
    from app.services.bots.ml_job_store import is_ml_job_cancelled

    if is_ml_job_cancelled(jid):
        cleanup_ml_progress(progress_path)
        return _finalize_job(jid, {"ok": False, "cancelled": True, "error": "cancelled"})

    stop = asyncio.Event()
    poll_task = asyncio.create_task(
        _poll_progress_loop(jid, progress_path, stop, event_bus=event_bus),
    )

    out: dict[str, Any] = {"ok": False, "error": "validate did not complete"}
    try:
        result = await _run_in_pool(
            run_validate_job,
            strategy,
            symbol,
            candles,
            cfg,
            n_folds,
            mode,
            run_pbo,
            pbo_segments,
            job_id=jid,
        )
        out = _finalize_job(
            jid,
            result if isinstance(result, dict) else {"ok": False, "error": "invalid validate result"},
        )
    except asyncio.CancelledError:
        from app.services.bots.ml_job_store import finish_ml_job, request_ml_job_cancel
        request_ml_job_cancel(jid)
        finish_ml_job(jid, "cancelled", error="cancelled")
        raise
    except Exception as exc:
        from app.services.bots.ml_job_store import finish_ml_job
        logger.exception("ML validate job %s failed", jid)
        finish_ml_job(jid, "error", error=str(exc))
        out = {"ok": False, "error": str(exc), "job_id": jid}
        raise
    finally:
        stop.set()
        try:
            await poll_task
        except Exception:
            pass
        cleanup_ml_progress(progress_path)

    # Validate may persist WF/PBO onto metadata — refresh caches only on success.
    if isinstance(out, dict) and out.get("ok") and not out.get("cancelled"):
        try:
            invalidate_strategy_model_caches(strategy, symbol)
        except Exception:
            logger.exception("Parent model cache invalidate failed after validate %s/%s", strategy, symbol)
    return out
