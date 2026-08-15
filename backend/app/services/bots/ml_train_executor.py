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

# Deep / RL trainers — process pool by default (MEMORY #41); set
# ML_TRAIN_TORCH_IN_PROCESS=1 to opt into in-process threads for debugging.
TORCH_TRAIN_STRATEGIES = frozenset({
    "LSTM_DIRECTION",
    "RL_PPO_AGENT",
    "TCN_MULTI_HORIZON",
    "VAE_REGIME_DETECTOR",
    "TRANSFORMER_SIGNAL",
    "GNN_CROSS_ASSET",
})


def _assess_candle_integrity(candles: list, timeframe: str | None = None) -> dict[str, Any]:
    """Compute candle integrity metrics for the train-time DQ gate.

    Crypto 1m feeds routinely skip empty minutes, so a naive ``delta > 1.5×``
    gap *count* rate falsely flags healthy histories (e.g. ADA 50k bars at
    ~20% mild gaps). We therefore report:

    - ``gap_rate``: mild gaps (>1.5× expected) — soft warning only
    - ``severe_gap_rate``: large holes (>5× expected) — hard-gate candidate
    - ``missing_frac``: 1 − coverage over the span — hard-gate candidate

    Returns bars/gaps/severe_gaps/gap_rate/severe_gap_rate/coverage/missing_frac.
    """
    times = []
    for c in candles or []:
        if not isinstance(c, dict):
            continue
        t = c.get("time") or c.get("timestamp") or c.get("t")
        if t is None:
            continue
        try:
            times.append(float(t))
        except (TypeError, ValueError):
            continue
    n = len(times)
    empty = {
        "bars": n,
        "gaps": 0,
        "severe_gaps": 0,
        "gap_rate": 0.0,
        "severe_gap_rate": 0.0,
        "coverage": 1.0,
        "missing_frac": 0.0,
        "expected_sec": _tf_seconds(timeframe),
    }
    if n < 2:
        return empty

    times.sort()
    # Normalize ms → seconds when the series looks like epoch-ms.
    if times[-1] > 1e12:
        times = [t / 1000.0 for t in times]

    deltas = [times[i + 1] - times[i] for i in range(n - 1)]
    deltas = [d for d in deltas if d > 0]
    if not deltas:
        return empty

    # Prefer configured TF interval; fall back to median when TF is unknown /
    # wrong so irregular series still get a sensible baseline.
    expected = _tf_seconds(timeframe)
    # Robust median without importing pandas (worker cold-start).
    sd = sorted(deltas)
    median = sd[len(sd) // 2]
    if expected <= 0:
        expected = median
    # If median is wildly different from TF (e.g. HT bars labeled 1m), trust median.
    if median > 0 and (median < expected * 0.4 or median > expected * 2.5):
        expected = median

    mild_thresh = expected * 1.5
    severe_thresh = expected * 5.0
    mild = sum(1 for d in deltas if d > mild_thresh)
    severe = sum(1 for d in deltas if d > severe_thresh)
    span = times[-1] - times[0]
    expected_bars = (span / expected) + 1.0 if expected > 0 else float(n)
    coverage = min(1.0, n / expected_bars) if expected_bars > 0 else 1.0
    missing_frac = max(0.0, 1.0 - coverage)
    return {
        "bars": n,
        "gaps": mild,
        "severe_gaps": severe,
        "gap_rate": round(mild / max(1, len(deltas)), 4),
        "severe_gap_rate": round(severe / max(1, len(deltas)), 4),
        "coverage": round(coverage, 4),
        "missing_frac": round(missing_frac, 4),
        "expected_sec": expected,
    }


def _tf_seconds(timeframe: str | None) -> float:
    tf = str(timeframe or "1m").strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    try:
        unit = tf[-1]
        val = float(tf[:-1]) if len(tf) > 1 else 1.0
        return val * mult.get(unit, 60)
    except (ValueError, IndexError):
        return 60.0


def _dq_train_should_block(dq: dict[str, Any], cfg: dict) -> tuple[bool, str]:
    """Decide whether integrity metrics warrant a hard training block.

    Mild empty-minute gaps (common on crypto) only warn. Hard-block when the
    series is actually sparse: high missing coverage or frequent severe holes.
    Default mode is soft-warn (``"warn"`` / unset) — never blocks.
    """
    mode = cfg.get("dq_train_gate", "warn")
    if mode in (False, 0, "0", "false", "False", "off", "warn", "Warn", "WARN", None):
        return False, ""

    max_missing = float(cfg.get("dq_max_missing_frac", 0.40))
    max_severe = float(cfg.get("dq_max_severe_gap_rate", 0.10))
    # strict / hard: also fail on mild gap_rate (legacy 5% behaviour).
    # true / True / 1 / "on": hard-block on coverage/severe only (not mild).
    strict = str(mode).lower() in ("strict", "hard")
    enabled = strict or mode in (True, 1, "1", "true", "True", "on", "yes")
    if not enabled:
        return False, ""

    max_mild = float(cfg.get("dq_max_gap_rate", 0.05))

    missing = float(dq.get("missing_frac") or 0.0)
    severe = float(dq.get("severe_gap_rate") or 0.0)
    mild = float(dq.get("gap_rate") or 0.0)

    if missing > max_missing:
        return True, (
            f"missing_frac={missing:.3f} over {dq.get('bars')} bars "
            f"(max {max_missing:.3f})"
        )
    if severe > max_severe:
        return True, (
            f"severe_gap_rate={severe:.3f} over {dq.get('bars')} bars "
            f"(max {max_severe:.3f})"
        )
    if strict and mild > max_mild:
        return True, (
            f"gap_rate={mild:.3f} over {dq.get('bars')} bars "
            f"(strict max {max_mild:.3f})"
        )
    return False, ""


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


def resolve_ml_train_max_workers() -> int:
    """Resolve process-pool worker count (Opt #1 auto-scale).

    - Explicit integer env → used as-is (min 1)
    - ``auto`` → conservative: 2 only when CUDA is available **and**
      ``ML_TRAIN_RSS_LIMIT_MB >= 6144``; otherwise 1
    - Default shipped value remains 1 (see config.py)
    """
    from app.config import ML_TRAIN_MAX_WORKERS_RAW, ML_TRAIN_RSS_LIMIT_MB

    raw = (ML_TRAIN_MAX_WORKERS_RAW or "1").strip().lower()
    if raw in ("auto", "scale"):
        rss = max(0, int(ML_TRAIN_RSS_LIMIT_MB or 0))
        cuda_ok = False
        try:
            import torch

            cuda_ok = bool(torch.cuda.is_available())
        except Exception:
            cuda_ok = False
        if cuda_ok and rss >= 6144:
            return 2
        return 1
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _max_workers() -> int:
    return resolve_ml_train_max_workers()


def use_process_pool_for_strategy(strategy: str | None) -> bool:
    """Whether this strategy should run in ProcessPoolExecutor.

    Default (MEMORY #41): everything pool-bound, including Torch/CUDA jobs, so
    deep-train RSS spikes stay out of the live feed/OMS process. Operators can
    opt Torch strategies back into in-process threads
    (``ML_TRAIN_TORCH_IN_PROCESS=1``) for debugging. Individual strategies that
    are unstable under spawn (``ML_TRAIN_IN_PROCESS_STRATEGIES``, default
    RL_PPO_AGENT) stay in-process regardless.
    """
    from app.config import (
        ML_TRAIN_IN_PROCESS_STRATEGIES,
        ML_TRAIN_PROCESS_ISOLATION,
        ML_TRAIN_TORCH_IN_PROCESS,
    )

    if not ML_TRAIN_PROCESS_ISOLATION:
        return False
    strat = str(strategy or "").upper()
    if strat in ML_TRAIN_IN_PROCESS_STRATEGIES:
        return False
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
            try:
                _pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                logger.debug("ML train pool shutdown error", exc_info=True)
            _pool = None


def reset_ml_train_pool(*, reason: str = "") -> None:
    """Drop a broken ProcessPoolExecutor so the next job gets a fresh pool.

    After an abrupt worker kill (OOM, Task Manager, admin cancel), Python marks
    the pool unusable: \"A child process terminated abruptly, the process pool
    is not usable anymore\". Clearing ``_pool`` lets ``get_ml_train_pool``
    create a new executor without restarting the whole backend.
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            return
        try:
            _pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            logger.debug("ML train pool reset shutdown error", exc_info=True)
        _pool = None
        logger.warning(
            "ML train process pool reset%s",
            f" ({reason})" if reason else "",
        )


def _is_broken_pool_error(exc: BaseException) -> bool:
    name = type(exc).__name__
    msg = str(exc).lower()
    return (
        name in ("BrokenProcessPool", "BrokenExecutor")
        or "process pool is not usable" in msg
        or "terminated abruptly" in msg
        or ("child process" in msg and "abruptly" in msg)
    )


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

    # Equity RTH filter for training — crypto untouched. Opt out with rth_only_training=false.
    # Copy bars before stamping `_symbol` so caller-owned candle lists are not mutated.
    bars = [dict(c) if isinstance(c, dict) else c for c in (candles or [])]
    if cfg.get("rth_only_training", True):
        try:
            from app.services.altdata.calendar import filter_equity_rth_candles

            before = len(bars)
            bars = filter_equity_rth_candles(symbol, bars)
            if len(bars) != before:
                logger.info(
                    "RTH filter %s: %s → %s bars",
                    symbol,
                    before,
                    len(bars),
                )
        except Exception:
            logger.debug("RTH candle filter skipped", exc_info=True)
    for bar in bars:
        if isinstance(bar, dict):
            bar.setdefault("_symbol", symbol)
    candles = bars
    cfg.setdefault("symbol", symbol)

    # Train-time data-quality gate. Crypto 1m histories skip empty minutes, so
    # mild gap_rate alone must not abort Apply & Retrain / champion trains.
    # Default is soft-warn only (dq_train_gate="warn"); hard-block requires
    # dq_train_gate=true/strict. Skipped for walk-forward fold exports.
    gate_mode = cfg.get("dq_train_gate", "warn")
    if gate_mode not in (False, 0, "0", "false", "False", "off", None) and not cfg.get("_wf_mode") and candles:
        try:
            dq = _assess_candle_integrity(candles, cfg.get("timeframe"))
            # Preserve explicit True as "enabled hard-ish" via should_block defaults.
            block_cfg = {**cfg, "dq_train_gate": gate_mode}
            block, reason = _dq_train_should_block(dq, block_cfg)
            if block:
                write_ml_progress(progress_path, pct=100, phase="dq_blocked", detail=symbol)
                return {
                    "ok": False,
                    "error": (
                        f"Data-quality gate blocked training for {symbol}: {reason}. "
                        "Re-ingest candles or set dq_train_gate=false to override."
                    ),
                    "dq": dq,
                    "symbol": symbol,
                }
            mild = float(dq.get("gap_rate") or 0.0)
            if mild > float(cfg.get("dq_max_gap_rate", 0.05)):
                logger.warning(
                    "DQ soft-warn %s: mild gap_rate=%.3f coverage=%.3f severe=%.3f "
                    "(training continues; set dq_train_gate=strict to hard-fail)",
                    symbol,
                    mild,
                    float(dq.get("coverage") or 0.0),
                    float(dq.get("severe_gap_rate") or 0.0),
                )
                cfg["_dq_soft_warn"] = dq
        except Exception:
            logger.debug("DQ train gate skipped", exc_info=True)

    from app.services.bots.ml_registry import get_trainer_import

    entry = get_trainer_import(strat)
    if not entry:
        from app.services.bots.ml_retrain_scheduler import lab_train_unsupported_error
        return {"ok": False, "error": lab_train_unsupported_error(strat)}
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

    # Attach FIT/EMBARGO/HOLDOUT calendar onto champion metadata (all strategies).
    cal = cfg.get("_data_calendar") if isinstance(cfg.get("_data_calendar"), dict) else None
    if isinstance(result, dict) and result.get("ok") and cal and not cfg.get("_wf_mode"):
        try:
            from app.services.bots.ml_data_calendar import merge_calendar_into_metadata
            from app.services.bots.ml_model_artifacts import model_root_for
            import json as _json
            import os as _os

            merged = merge_calendar_into_metadata(result, cal)
            result.update({
                k: merged[k]
                for k in (
                    "data_calendar", "fit_end_ts", "holdout_start_ts",
                    "holdout_end_ts", "holdout_days", "calendar_version",
                )
                if k in merged
            })
            root = model_root_for(strat, symbol, cfg.get("timeframe"))
            if root:
                meta_path = _os.path.join(root, "metadata.json")
                if _os.path.isfile(meta_path):
                    with open(meta_path, encoding="utf-8") as fh:
                        disk = _json.load(fh)
                    if isinstance(disk, dict):
                        disk = merge_calendar_into_metadata(disk, cal)
                        with open(meta_path, "w", encoding="utf-8") as fh:
                            _json.dump(disk, fh, indent=2)
        except Exception:
            logger.exception("Failed to stamp data_calendar on %s/%s", strat, symbol)

    done_detail = "complete"
    if isinstance(result, dict):
        m = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        if result.get("early_stopped") or m.get("early_stopped"):
            done_detail = (
                m.get("early_stop_reason")
                or result.get("early_stop_reason")
                or (
                    f"early stop @ {m.get('epochs_trained') or result.get('epochs_trained')}"
                    f"/{m.get('epochs_budget') or '?'}"
                )
            )
    write_ml_progress(progress_path, pct=100, phase="done", detail=str(done_detail)[:160])
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
    strat_u = str(strategy or "").upper()
    # Deploy-grade (capacity parity) stays live_aligned. Lean/exploratory validate
    # may opt into research_fast via explicit sim_mode or ML_EXPLORATORY_SIM_MODE.
    wf_parity_early = bool(cfg.get("wf_capacity_parity", True))
    if "sim_mode" not in cfg:
        if wf_parity_early:
            cfg["sim_mode"] = "live_aligned"
        else:
            try:
                from app.config import ML_EXPLORATORY_SIM_MODE

                explor = (ML_EXPLORATORY_SIM_MODE or "").strip().lower()
            except Exception:
                explor = ""
            if explor in ("research", "research_fast"):
                cfg["sim_mode"] = explor
            else:
                cfg.setdefault("sim_mode", "live_aligned")
    # Lean GBM default only for fast Validate / Optuna on HistGBM.
    if (
        strat_u == "ML_SIGNAL_BOOST"
        and not bool(cfg.get("wf_capacity_parity", True))
    ):
        cfg.setdefault("max_iter", 40)
    # Transformer OOS uses in-memory torch; other strategies still export fold
    # ONNX for strategy.evaluate — champion is restored after WF below.
    if strat_u == "TRANSFORMER_SIGNAL":
        cfg.setdefault("skip_onnx_export", True)

    champion_snap = _backup_live_champion(strat_u, symbol, cfg.get("timeframe"))
    # Capacity parity (default True): folds use production-scale epochs /
    # timesteps so OOS metrics reflect Lab Train. Fast mode only when the
    # caller explicitly sets wf_capacity_parity=false (e.g. Optuna screen).
    wf_parity = bool(cfg.get("wf_capacity_parity", True))
    cfg["wf_capacity_parity"] = wf_parity

    _WF_EPOCH_CAPS = {
        "LSTM_DIRECTION": 12,
        "TRANSFORMER_SIGNAL": 8,
        "TCN_MULTI_HORIZON": 10,
        "VAE_REGIME_DETECTOR": 10,
        "GNN_CROSS_ASSET": 8,
    }
    if strat_u in _WF_EPOCH_CAPS:
        cfg.setdefault("wf_use_gpu", True)
        if wf_parity:
            # Keep caller / Lab Advanced epochs — do not crush to fast-fold caps.
            cfg.setdefault("wf_epochs", int(cfg.get("epochs") or _WF_EPOCH_CAPS[strat_u]))
        else:
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
        cfg.setdefault("wf_use_gpu", True)
        cfg.setdefault("skip_onnx_export", True)
        cfg.setdefault("skip_snapshot", True)
        cfg.setdefault("max_episode_steps", 2048)
        if wf_parity:
            # Match Lab Train Advanced defaults unless the request already set them.
            cfg.setdefault("total_timesteps", 200_000)
            cfg.setdefault("n_steps", 2048)
            cfg.setdefault("ppo_epochs", 10)
            cfg.setdefault("hidden_dim", 256)
        else:
            # Legacy fast interactive Validate.
            cfg.setdefault("total_timesteps", 4096)
            cfg.setdefault("n_steps", 512)
            cfg.setdefault("ppo_epochs", 2)
            cfg.setdefault("hidden_dim", 64)
            try:
                user_vmax = int(cfg.get("validate_max_bars") or 0)
            except (TypeError, ValueError):
                user_vmax = 0
            if user_vmax <= 0:
                cfg["validate_max_bars"] = 2000
        if run_pbo and not bool(cfg.get("force_pbo")):
            run_pbo = False
            cfg["_pbo_skipped"] = "rl_too_expensive"

    max_bars = int(cfg.get("validate_max_bars", 12_000 if wf_parity else 2500))
    # Fast mode keeps a hard ceiling so interactive Validate stays responsive.
    # Capacity parity honors Lab window depth up to the Train hard max.
    if not wf_parity and strat_u in TORCH_TRAIN_STRATEGIES:
        max_bars = min(max_bars, 12_000)
    elif wf_parity:
        max_bars = min(max_bars, 100_000)
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


def _parent_trim_validate_candles(strategy: str | None, candles: list, cfg: dict) -> list:
    """MEMORY #41 — mirror the worker's deep-WF bar cap in the parent so
    pool-bound validate jobs pickle far fewer candles (Windows spawn concern).

    Only applies when the job is actually pool-bound for a Torch strategy;
    anything else passes through untouched.
    """
    if not use_process_pool_for_strategy(strategy):
        return candles
    if str(strategy or "").upper() not in TORCH_TRAIN_STRATEGIES:
        return candles
    parity = bool(cfg.get("wf_capacity_parity", True))
    try:
        requested = int(cfg.get("validate_max_bars", 12_000 if parity else 2500))
    except (TypeError, ValueError):
        requested = 12_000 if parity else 2500
    max_bars = min(requested, 100_000 if parity else 12_000)
    if len(candles or []) > max_bars:
        return candles[-max_bars:]
    return candles


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
    cfg["job_id"] = jid
    try:
        from app.services.bots.ml_job_store import load_ml_job_checkpoint
        cp = load_ml_job_checkpoint(jid)
        if isinstance(cp, dict) and cp.get("study_path"):
            cfg.setdefault("resume_study_path", cp["study_path"])
    except Exception:
        pass
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
        done_detail = "complete"
        m = out.get("metrics") if isinstance(out.get("metrics"), dict) else {}
        if out.get("early_stopped") or m.get("early_stopped"):
            done_detail = (
                m.get("early_stop_reason")
                or out.get("early_stop_reason")
                or (
                    f"early stop @ {m.get('epochs_trained') or out.get('epochs_trained')}"
                    f"/{m.get('epochs_budget') or '?'}"
                )
            )
        update_ml_job_progress(
            job_id, {"pct": 100, "phase": "done", "detail": str(done_detail)[:160]},
        )
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

    All strategies default to the pool (MEMORY #41); Torch/RL only goes to
    ``asyncio.to_thread`` when ``ML_TRAIN_TORCH_IN_PROCESS=1`` (debugging).
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
        last_exc: BaseException | None = None
        for attempt in range(2):
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
                last_exc = exc
                # Broken pool after worker kill: recreate once. Never fall back to
                # in-process torch (MEMORY #9/#27) — that would OOM the live feed.
                if _is_broken_pool_error(exc):
                    logger.error(
                        "ML process pool broken%s: %s",
                        " — recreating and retrying once" if attempt == 0 else " (after retry)",
                        exc,
                    )
                    reset_ml_train_pool(reason=str(exc))
                    if attempt == 0:
                        continue
                    raise
                logger.error(
                    "ML process pool failed — not falling back to in-process thread: %s",
                    exc,
                )
                raise
        assert last_exc is not None
        raise last_exc

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

            get_retrain_scheduler().record_retrain(
                strategy, symbol, timeframe=cfg.get("timeframe"),
            )
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

    # MEMORY #41 — deep WF validates cap at ≤12k bars inside the worker; trim in
    # the parent too so pool-bound jobs pickle far less (Windows spawn concern).
    candles = _parent_trim_validate_candles(strategy, candles, cfg)

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


def run_hyperparam_sweep_job(
    strategy: str,
    symbol: str,
    candles: list,
    config: dict | None,
) -> dict[str, Any]:
    """Picklable worker entry for Optuna ML hyperparameter sweeps."""
    from app.services.bots.ml_hyperparam_sweep import run_ml_hyperparam_sweep
    from app.services.bots.ml_job_progress import (
        ml_cancel_requested,
        progress_path_from_config,
        write_ml_progress,
    )

    cfg = dict(config or {})
    progress_path = progress_path_from_config(cfg)
    if ml_cancel_requested(progress_path):
        return {"ok": False, "cancelled": True, "error": "cancelled"}

    write_ml_progress(progress_path, pct=2, phase="hyperparam_start", detail=str(strategy))

    def _progress(payload: dict) -> None:
        data = dict(payload or {})
        write_ml_progress(
            progress_path,
            pct=int(data.get("pct") or 0),
            phase=str(data.get("phase") or "hyperparam_trial"),
            detail=str(data.get("detail") or "")[:160],
            extra=data,
        )

    def _cancel() -> bool:
        return ml_cancel_requested(progress_path)

    result = run_ml_hyperparam_sweep(
        strategy,
        symbol,
        candles,
        config=cfg,
        max_trials=int(cfg.get("max_trials") or 20),
        time_budget_sec=float(cfg.get("time_budget_sec") or 600),
        patience=int(cfg.get("patience") or 8),
        multi_fidelity=bool(cfg.get("multi_fidelity", True)),
        screen_fraction=float(cfg.get("screen_fraction") or 0.25),
        promote_top_k=int(cfg.get("promote_top_k") or 3),
        custom_search_space=cfg.get("custom_search_space")
        if isinstance(cfg.get("custom_search_space"), dict)
        else None,
        progress_cb=_progress,
        cancel_cb=_cancel,
        train_fn=lambda sym, bars, config=None: run_train_job(
            str(strategy).upper(), sym, bars, config,
        ),
        objective_kind=str(cfg.get("objective_kind") or "purged_cv"),
        cv_folds_screen=int(cfg.get("cv_folds_screen") or 2),
        cv_folds_full=int(cfg.get("cv_folds_full") or 3),
        job_id=str(cfg.get("job_id") or "") or None,
        resume_study_path=str(cfg.get("resume_study_path") or "") or None,
    )
    if isinstance(result, dict) and result.get("ok"):
        write_ml_progress(
            progress_path,
            pct=100,
            phase="done",
            detail=f"best={result.get('best_score')} · {result.get('trials_completed')} trials",
        )
        # Persist as optimization run for apply-config / retrain feedback
        try:
            from app.services.bots.optimization_store import save_optimization_run

            run_id = save_optimization_run(
                symbol=symbol,
                strategy=str(strategy).upper(),
                objective="ml_val_score",
                request={
                    "kind": "ml_hyperparam_sweep",
                    "max_trials": cfg.get("max_trials"),
                    "time_budget_sec": cfg.get("time_budget_sec"),
                    "timeframe": cfg.get("timeframe"),
                    "importance_ranking": result.get("importance_ranking"),
                    "convergence": result.get("convergence"),
                },
                results=result.get("trial_history") or [],
                best_config=result.get("best_hyperparams") or {},
            )
            result["optimization_run_id"] = run_id
        except Exception:
            logger.exception("Failed to persist hyperparam sweep run")
    else:
        write_ml_progress(
            progress_path,
            pct=100,
            phase="error",
            detail=str((result or {}).get("error") or "sweep failed")[:160],
        )
    return result if isinstance(result, dict) else {"ok": False, "error": "invalid sweep result"}


async def submit_hyperparam_sweep_job(
    strategy: str,
    symbol: str,
    candles: list,
    config: dict | None,
    *,
    job_id: str | None = None,
    event_bus: Any = None,
) -> dict[str, Any]:
    """Run hyperparam sweep in process/thread pool with progress polling."""
    from app.services.bots.ml_job_progress import cleanup_ml_progress, write_ml_progress

    jid, cfg, progress_path = _prepare_job_config(
        "hyperparam_sweep", strategy, symbol, config, job_id=job_id,
    )
    from app.services.bots.ml_job_store import is_ml_job_cancelled

    if is_ml_job_cancelled(jid):
        cleanup_ml_progress(progress_path)
        return _finalize_job(jid, {"ok": False, "cancelled": True, "error": "cancelled"})

    write_ml_progress(
        progress_path,
        pct=0,
        phase="dispatch",
        detail=f"hyperparam · {len(candles or [])} bars",
    )

    stop = asyncio.Event()
    poll_task = asyncio.create_task(
        _poll_progress_loop(jid, progress_path, stop, event_bus=event_bus),
    )
    out: dict[str, Any] = {"ok": False, "error": "hyperparam sweep did not complete"}
    try:
        result = await _run_in_pool(
            run_hyperparam_sweep_job,
            strategy,
            symbol,
            candles,
            cfg,
            job_id=jid,
            strategy=strategy,
        )
        out = _finalize_job(
            jid,
            _with_training_window(cfg, result if isinstance(result, dict) else {"ok": False}),
        )
    except asyncio.CancelledError:
        from app.services.bots.ml_job_store import finish_ml_job, request_ml_job_cancel
        request_ml_job_cancel(jid)
        finish_ml_job(jid, "cancelled", error="cancelled")
        raise
    except Exception as exc:
        from app.services.bots.ml_job_store import finish_ml_job
        logger.exception("ML hyperparam sweep job %s failed", jid)
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

    return out
