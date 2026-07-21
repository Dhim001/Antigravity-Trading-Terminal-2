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

# Deep / RL trainers — prefer in-process threads (see ML_TRAIN_TORCH_IN_PROCESS).
TORCH_TRAIN_STRATEGIES = frozenset({
    "LSTM_DIRECTION",
    "RL_PPO_AGENT",
    "TCN_MULTI_HORIZON",
    "VAE_REGIME_DETECTOR",
    "TRANSFORMER_SIGNAL",
    "GNN_CROSS_ASSET",
})


def _backup_live_champion(strategy: str, symbol: str, timeframe: str | None) -> dict[str, Any] | None:
    """Copy live root files aside so WF fold exports cannot leave a tiny champion."""
    import os
    import shutil
    import tempfile

    from app.services.bots.ml_model_artifacts import model_root_for

    root = model_root_for(strategy, symbol, timeframe)
    if not root or not os.path.isdir(root):
        return None
    files = [
        name for name in os.listdir(root)
        if os.path.isfile(os.path.join(root, name))
    ]
    if not files:
        return None
    tmp = tempfile.mkdtemp(prefix="ml_champion_")
    for name in files:
        shutil.copy2(os.path.join(root, name), os.path.join(tmp, name))
    return {"root": root, "tmp": tmp, "files": files}


def _restore_live_champion(snap: dict[str, Any] | None) -> None:
    import os
    import shutil

    if not isinstance(snap, dict):
        return
    root = snap.get("root")
    tmp = snap.get("tmp")
    files = snap.get("files") or []
    if not root or not tmp:
        return
    try:
        for name in files:
            src = os.path.join(tmp, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(root, name))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _max_workers() -> int:
    from app.config import ML_TRAIN_MAX_WORKERS
    return max(1, int(ML_TRAIN_MAX_WORKERS))


def use_process_pool_for_strategy(strategy: str | None) -> bool:
    """Whether this strategy should run in ProcessPoolExecutor.

    Torch/CUDA jobs default to ``asyncio.to_thread`` so we do not pickle huge
    candle lists into a spawn worker (looks like a hang from pct=0) and avoid
    Windows CUDA+ProcessPool fragility.
    """
    from app.config import ML_TRAIN_PROCESS_ISOLATION, ML_TRAIN_TORCH_IN_PROCESS

    if not ML_TRAIN_PROCESS_ISOLATION:
        return False
    strat = str(strategy or "").upper()
    if strat in TORCH_TRAIN_STRATEGIES and ML_TRAIN_TORCH_IN_PROCESS:
        return False
    return True


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
    """Picklable top-level entry — runs inside the worker process or thread."""
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
    write_ml_progress(progress_path, pct=3, phase="import", detail=mod_name.rsplit(".", 1)[-1])
    import importlib
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, fn_name)
    write_ml_progress(
        progress_path,
        pct=5,
        phase="train",
        detail=f"{strat} · {len(candles or [])} bars",
    )
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
    # Transformer OOS uses in-memory torch; other strategies still export fold
    # ONNX for strategy.evaluate — champion is restored after WF below.
    if str(strategy or "").upper() == "TRANSFORMER_SIGNAL":
        cfg.setdefault("skip_onnx_export", True)

    strat_u = str(strategy or "").upper()
    champion_snap = _backup_live_champion(strat_u, symbol, cfg.get("timeframe"))
    # Deep fold trains: prefer GPU + short epoch budgets (Lab may send train epochs).
    _WF_EPOCH_CAPS = {
        "LSTM_DIRECTION": 12,
        "TRANSFORMER_SIGNAL": 8,
        "TCN_MULTI_HORIZON": 10,
        "VAE_REGIME_DETECTOR": 10,
        "GNN_CROSS_ASSET": 8,
    }
    if strat_u in _WF_EPOCH_CAPS:
        cfg.setdefault("wf_use_gpu", True)
        cfg.setdefault("wf_epochs", _WF_EPOCH_CAPS[strat_u])
        try:
            ep = int(cfg.get("epochs", cfg["wf_epochs"]))
        except (TypeError, ValueError):
            ep = int(cfg["wf_epochs"])
        cfg["epochs"] = min(max(1, ep), int(cfg["wf_epochs"]))
        # CSCV PBO re-trains deep models many times — skip unless force_pbo.
        if run_pbo and not bool(cfg.get("force_pbo")):
            run_pbo = False
            cfg["_pbo_skipped"] = "deep_too_expensive"

    if strat_u == "RL_PPO_AGENT":
        cfg.setdefault("total_timesteps", 2048)
        cfg.setdefault("n_steps", 512)
        cfg.setdefault("ppo_epochs", 2)
        cfg.setdefault("hidden_dim", 64)
        cfg.setdefault("validate_max_bars", 1200)
        cfg.setdefault("wf_use_gpu", True)
        if run_pbo and not bool(cfg.get("force_pbo")):
            run_pbo = False
            cfg["_pbo_skipped"] = "rl_too_expensive"

    max_bars = int(cfg.get("validate_max_bars", 2500))
    # Deep WF stays bounded, but HTF Lab windows may request more than the old 2.5k floor.
    if strat_u in TORCH_TRAIN_STRATEGIES:
        max_bars = min(max_bars, 12_000)
    if len(candles) > max_bars:
        candles = candles[-max_bars:]

    try:
        wf_result = walk_forward_ml_train(
            strategy, symbol, candles,
            config=cfg, n_folds=n_folds, mode=mode,
        )
    finally:
        _restore_live_champion(champion_snap)
    result = dict(wf_result)
    if cfg.get("timeframe") and "timeframe" not in result:
        result["timeframe"] = cfg.get("timeframe")
    if result.get("cancelled"):
        write_ml_progress(progress_path, pct=100, phase="cancelled", detail="cancelled")
        return result

    agg = result.get("aggregate") if isinstance(result.get("aggregate"), dict) else {}
    if result.get("ok") and agg.get("mean_oos_accuracy") is not None:
        result.setdefault("mean_accuracy", agg.get("mean_oos_accuracy"))

    if cfg.get("_pbo_skipped"):
        skip_reason = cfg.get("_pbo_skipped")
        if skip_reason == "deep_too_expensive":
            pbo_err = (
                "PBO skipped for deep models on interactive validate. "
                "Set config.force_pbo=true to run anyway."
            )
        else:
            pbo_err = (
                "PBO skipped for RL_PPO_AGENT (too slow for interactive validate). "
                "Set config.force_pbo=true to run anyway."
            )
        result["pbo"] = {
            "ok": False,
            "skipped": True,
            "error": pbo_err,
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
                timeframe=cfg.get("timeframe"),
            )
            result["validation_persisted"] = persist_res
            if not persist_res.get("ok"):
                logger.error(
                    "ML validation metrics ok but failed to persist stamp for %s/%s: %s",
                    strategy,
                    symbol,
                    persist_res.get("error"),
                )
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
    from app.services.bots.ml_job_store import finish_ml_job, is_ml_job_cancelled, update_ml_job_progress

    out = dict(result) if isinstance(result, dict) else {"ok": False, "error": "invalid result"}
    out.setdefault("job_id", job_id)

    if is_ml_job_cancelled(job_id) or out.get("cancelled"):
        out["cancelled"] = True
        out["ok"] = False
        out.setdefault("error", "cancelled")
        update_ml_job_progress(job_id, {"pct": 100, "phase": "cancelled", "detail": "cancelled"})
        finish_ml_job(job_id, "cancelled", result=out, error="cancelled")
        return out

    if out.get("ok"):
        update_ml_job_progress(job_id, {"pct": 100, "phase": "done", "detail": "complete"})
        finish_ml_job(job_id, "done", result=out)
    else:
        update_ml_job_progress(job_id, {"pct": 100, "phase": "error", "detail": str(out.get("error") or "failed")})
        finish_ml_job(job_id, "error", result=out, error=str(out.get("error") or "failed"))
    return out


def _with_training_window(cfg: dict, result: Any) -> dict[str, Any]:
    """Stamp Lab training-window metadata onto job results for the UI."""
    out = dict(result) if isinstance(result, dict) else {"ok": False, "error": "invalid result"}
    if isinstance(cfg, dict):
        tw = cfg.get("_training_window")
        if isinstance(tw, dict):
            out.setdefault("training_window", tw)
        if cfg.get("timeframe"):
            out.setdefault("timeframe", cfg.get("timeframe"))
    return out


async def _run_in_pool(fn, *args, job_id: str | None = None, strategy: str | None = None):
    """Submit to process pool (or thread); track Future for cancel.

    Torch/RL strategies default to ``asyncio.to_thread`` (see
    ``use_process_pool_for_strategy``) so Lab Train does not hang while
    pickling huge candle lists into a spawn worker.
    """
    from app.services.bots.ml_job_store import (
        attach_ml_job_future,
        is_ml_job_cancelled,
        mark_ml_job_running,
    )

    # Never start work the user already cancelled (queued cancel race).
    if job_id and is_ml_job_cancelled(job_id):
        return {"ok": False, "cancelled": True, "error": "cancelled"}

    use_pool = use_process_pool_for_strategy(strategy)
    if use_pool:
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
    # Isolation off or Torch-in-process — cooperative cancel still via progress file.
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
    from app.services.bots.ml_job_progress import cleanup_ml_progress, write_ml_progress
    from app.services.bots.ml_model_artifacts import invalidate_strategy_model_caches

    jid, cfg, progress_path = _prepare_job_config(
        "train", strategy, symbol, config, job_id=job_id,
    )
    from app.services.bots.ml_job_store import is_ml_job_cancelled

    if is_ml_job_cancelled(jid):
        cleanup_ml_progress(progress_path)
        return _finalize_job(jid, {"ok": False, "cancelled": True, "error": "cancelled"})

    # Progress before dispatch — ProcessPool pickle can take a long time with no worker yet.
    mode = "process" if use_process_pool_for_strategy(strategy) else "thread"
    write_ml_progress(
        progress_path,
        pct=0,
        phase="dispatch",
        detail=f"{mode} · {len(candles or [])} bars",
    )

    stop = asyncio.Event()
    poll_task = asyncio.create_task(
        _poll_progress_loop(jid, progress_path, stop, event_bus=event_bus),
    )

    out: dict[str, Any] = {"ok": False, "error": "train did not complete"}
    try:
        result = await _run_in_pool(
            run_train_job, strategy, symbol, candles, cfg, job_id=jid, strategy=strategy,
        )
        out = _finalize_job(
            jid,
            _with_training_window(cfg, result),
        )
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
    from app.services.bots.ml_job_progress import cleanup_ml_progress, write_ml_progress
    from app.services.bots.ml_model_artifacts import invalidate_strategy_model_caches

    jid, cfg, progress_path = _prepare_job_config(
        "validate", strategy, symbol, config, job_id=job_id,
    )
    from app.services.bots.ml_job_store import is_ml_job_cancelled

    if is_ml_job_cancelled(jid):
        cleanup_ml_progress(progress_path)
        return _finalize_job(jid, {"ok": False, "cancelled": True, "error": "cancelled"})

    exec_mode = "process" if use_process_pool_for_strategy(strategy) else "thread"
    write_ml_progress(
        progress_path,
        pct=0,
        phase="dispatch",
        detail=f"{exec_mode} · validate · {len(candles or [])} bars",
    )

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
            strategy=strategy,
        )
        out = _finalize_job(
            jid,
            _with_training_window(
                cfg,
                result if isinstance(result, dict) else {"ok": False, "error": "invalid validate result"},
            ),
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
