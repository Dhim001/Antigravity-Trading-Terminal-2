"""ML hyperparameter auto-tune (Optuna TPE + multi-fidelity screen).

Sweeps training hyperparameters for Lab ML strategies. Objective is validation
accuracy (or return score for RL), not in-sample fit.
"""

from __future__ import annotations

import copy
import logging
import time
from typing import Any, Callable

from app.services.bots.ml_registry import SWEEPABLE_ML_STRATEGIES

logger = logging.getLogger(__name__)

# Keys surfaced on live progress snapshots for the Auto-Tune UI.
_PROGRESS_METRIC_KEYS = (
    "accuracy",
    "f1",
    "val_accuracy",
    "oos_accuracy",
    "oos_f1",
    "sharpe",
    "pbo",
    "mean_return",
    "profit_factor",
)


def _pick_progress_metrics(result: dict[str, Any] | None) -> dict[str, float]:
    """Compact metric snapshot from a trial evaluate result."""
    if not isinstance(result, dict):
        return {}
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    agg = result.get("aggregate") if isinstance(result.get("aggregate"), dict) else {}
    out: dict[str, float] = {}
    for key in _PROGRESS_METRIC_KEYS:
        raw = metrics.get(key)
        if raw is None:
            raw = agg.get(key)
        if raw is None:
            continue
        try:
            out[key] = round(float(raw), 4)
        except (TypeError, ValueError):
            continue
    return out


def _trial_warning(result: dict[str, Any] | None, *, score: float | None = None) -> str | None:
    if not isinstance(result, dict):
        return None
    if not result.get("ok"):
        err = str(result.get("error") or "").strip()
        return err or "trial failed"
    if score is not None and score <= -1e8:
        return "objective unscored (fallback floor)"
    return None


def default_search_space(strategy: str) -> dict[str, Any]:
    """Research-backed default search spaces (user overrides merge on top)."""
    s = str(strategy or "").upper()
    if s == "ML_SIGNAL_BOOST":
        return {
            "gbm_max_depth": {"type": "int", "low": 2, "high": 8},
            "gbm_learning_rate": {"type": "float", "low": 0.01, "high": 0.2, "log": True},
            "gbm_max_iter": {"type": "int", "low": 100, "high": 500},
            "gbm_l2_reg": {"type": "float", "low": 0.0, "high": 5.0},
            "val_fraction": {"type": "float", "low": 0.15, "high": 0.3},
            "triple_barrier_atr_mult": {"type": "float", "low": 1.0, "high": 4.0},
        }
    if s in ("LSTM_DIRECTION", "TRANSFORMER_SIGNAL", "TCN_MULTI_HORIZON", "GNN_CROSS_ASSET"):
        space = {
            "learning_rate": {"type": "float", "low": 1e-4, "high": 5e-3, "log": True},
            "hidden_dim": {"type": "categorical", "choices": [64, 128, 256]},
            "epochs": {"type": "int", "low": 30, "high": 150},
            "batch_size": {"type": "categorical", "choices": [32, 64, 128, 256]},
            "lookback": {"type": "int", "low": 30, "high": 180},
            "num_layers": {"type": "categorical", "choices": [1, 2, 3, 4]},
            "early_stop_patience": {"type": "int", "low": 5, "high": 20},
        }
        if s == "TRANSFORMER_SIGNAL":
            space["d_model"] = {"type": "categorical", "choices": [64, 128, 256]}
        if s == "GNN_CROSS_ASSET":
            space.pop("lookback", None)
            space["n_heads"] = {"type": "categorical", "choices": [2, 4, 8]}
        return space
    if s == "RL_PPO_AGENT":
        return {
            "learning_rate": {"type": "float", "low": 1e-4, "high": 1e-3, "log": True},
            "clip_epsilon": {"type": "float", "low": 0.1, "high": 0.3},
            "ent_coef": {"type": "float", "low": 0.01, "high": 0.05, "log": True},
            "n_steps": {"type": "categorical", "choices": [512, 1024, 2048, 4096]},
            "hidden_dim": {"type": "categorical", "choices": [64, 128, 256]},
            "max_episode_steps": {"type": "categorical", "choices": [512, 1024, 2048, 4096]},
            "total_timesteps": {"type": "categorical", "choices": [8192, 16384, 32768, 65536]},
        }
    if s == "VAE_REGIME_DETECTOR":
        return {
            "latent_dim": {"type": "categorical", "choices": [8, 16, 32, 64]},
            "anomaly_threshold": {"type": "float", "low": 1.0, "high": 4.0},
            "hidden_dim": {"type": "categorical", "choices": [64, 128, 256]},
            "learning_rate": {"type": "float", "low": 1e-4, "high": 5e-3, "log": True},
            "epochs": {"type": "int", "low": 40, "high": 120},
        }
    return {}


def merge_search_space(strategy: str, custom: dict | None) -> dict[str, Any]:
    space = default_search_space(strategy)
    if not isinstance(custom, dict):
        return space
    out = dict(space)
    for key, spec in custom.items():
        if isinstance(spec, dict):
            out[key] = {**(out.get(key) or {}), **spec}
        elif isinstance(spec, list) and spec:
            out[key] = {"type": "categorical", "choices": list(spec)}
    return out


def _suggest_from_space(trial, space: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key, spec in space.items():
        if not isinstance(spec, dict):
            continue
        kind = str(spec.get("type") or "float").lower()
        if kind == "categorical":
            choices = list(spec.get("choices") or [])
            if not choices:
                continue
            params[key] = trial.suggest_categorical(key, choices)
        elif kind == "int":
            lo = int(spec.get("low", 1))
            hi = int(spec.get("high", lo))
            if hi < lo:
                lo, hi = hi, lo
            params[key] = trial.suggest_int(key, lo, hi)
        else:
            lo = float(spec.get("low", 1e-4))
            hi = float(spec.get("high", 1e-2))
            if hi < lo:
                lo, hi = hi, lo
            log = bool(spec.get("log"))
            params[key] = trial.suggest_float(key, lo, hi, log=log)
    return params


def extract_objective_score(train_result: dict | None, strategy: str) -> float:
    """Higher is better. Returns a large negative on failure.

    Prefers purged walk-forward aggregate OOS metrics when present, then
    per-train validation metrics.
    """
    if not isinstance(train_result, dict) or not train_result.get("ok"):
        return -1e9

    # Purged / walk-forward aggregate (preferred)
    agg = train_result.get("aggregate") if isinstance(train_result.get("aggregate"), dict) else {}
    strat = str(strategy or "").upper()
    if strat == "RL_PPO_AGENT":
        for key in ("mean_oos_return_pct", "mean_return_pct", "best_mean_return"):
            src = agg if key.startswith("mean_oos") else (
                train_result.get("metrics") if isinstance(train_result.get("metrics"), dict) else {}
            )
            if src.get(key) is not None:
                try:
                    return float(src[key])
                except (TypeError, ValueError):
                    pass
        if agg.get("mean_oos_accuracy") is not None:
            return float(agg["mean_oos_accuracy"])
    else:
        if agg.get("mean_oos_accuracy") is not None:
            try:
                return float(agg["mean_oos_accuracy"])
            except (TypeError, ValueError):
                pass
        if agg.get("mean_oos_return_pct") is not None:
            try:
                return float(agg["mean_oos_return_pct"])
            except (TypeError, ValueError):
                pass

    metrics = train_result.get("metrics") if isinstance(train_result.get("metrics"), dict) else {}
    if strat == "RL_PPO_AGENT":
        for key in ("mean_return_pct", "best_mean_return", "mean_oos_return_pct"):
            if metrics.get(key) is not None:
                try:
                    return float(metrics[key])
                except (TypeError, ValueError):
                    pass
        if metrics.get("val_accuracy") is not None:
            return float(metrics["val_accuracy"])
        return -1e9
    if strat == "TCN_MULTI_HORIZON":
        for key in ("dir_acc_ret_15", "dir_acc_ret_5", "dir_acc_ret_60"):
            if metrics.get(key) is not None:
                return float(metrics[key])
        if metrics.get("val_mse") is not None:
            return -float(metrics["val_mse"])
        return -1e9
    if strat == "VAE_REGIME_DETECTOR":
        if metrics.get("val_loss") is not None:
            return -float(metrics["val_loss"])
        return -1e9
    for key in ("val_accuracy", "accuracy", "auc_roc"):
        if metrics.get(key) is not None:
            try:
                return float(metrics[key])
            except (TypeError, ValueError):
                pass
    if metrics.get("val_loss") is not None:
        return -float(metrics["val_loss"])
    return -1e9


def evaluate_trial_purged_cv(
    strategy: str,
    symbol: str,
    candles: list,
    config: dict | None,
    *,
    n_folds: int = 3,
    mode: str = "rolling",
    train_fn: Callable[..., dict] | None = None,
) -> dict[str, Any]:
    """Score hyperparams with purged walk-forward (AFML-style), not IS holdout.

    Falls back to a single ``train_fn`` call if WF cannot run (too few bars).
    """
    strat = str(strategy or "").upper()
    cfg = dict(config or {})
    cfg.setdefault("skip_persist", True)
    cfg.setdefault("skip_snapshot", True)
    cfg.setdefault("skip_refit", True)
    cfg["_wf_mode"] = True
    # Optuna trials must stay lean even if the Lab base config had parity on.
    cfg["wf_capacity_parity"] = False

    folds = max(2, min(5, int(n_folds or 3)))
    if len(candles or []) < 180:
        # Too short for meaningful WF — fall back to train holdout
        if train_fn is None:
            from app.services.bots.ml_train_executor import run_train_job
            train_fn = lambda sym, bars, config=None: run_train_job(strat, sym, bars, config)  # noqa: E731
        result = train_fn(symbol, candles, config={**cfg, "_wf_mode": False})
        if isinstance(result, dict):
            result = dict(result)
            result["objective_kind"] = "val_holdout_fallback"
        return result if isinstance(result, dict) else {"ok": False, "error": "train failed"}

    try:
        from app.services.bots.ml_walk_forward_validator import walk_forward_ml_train

        # Bound cost for Optuna trials
        max_bars = int(cfg.get("hyperparam_cv_max_bars") or (1200 if folds <= 2 else 2000))
        bars = candles[-max_bars:] if len(candles) > max_bars else candles
        wf = walk_forward_ml_train(
            strat, symbol, bars,
            config=cfg, n_folds=folds, mode=mode,
        )
        if isinstance(wf, dict):
            out = dict(wf)
            out["objective_kind"] = "purged_cv"
            # Mirror aggregate into metrics for extract_objective_score callers
            agg = out.get("aggregate") if isinstance(out.get("aggregate"), dict) else {}
            metrics = dict(out.get("metrics") or {}) if isinstance(out.get("metrics"), dict) else {}
            if agg.get("mean_oos_accuracy") is not None:
                metrics["val_accuracy"] = agg["mean_oos_accuracy"]
                metrics["mean_oos_accuracy"] = agg["mean_oos_accuracy"]
            if agg.get("mean_oos_return_pct") is not None:
                metrics["mean_oos_return_pct"] = agg["mean_oos_return_pct"]
            out["metrics"] = metrics
            return out
    except Exception as exc:
        logger.warning("Purged-CV trial failed for %s/%s: %s — falling back", strat, symbol, exc)

    if train_fn is None:
        from app.services.bots.ml_train_executor import run_train_job
        train_fn = lambda sym, bars, config=None: run_train_job(strat, sym, bars, config)  # noqa: E731
    result = train_fn(symbol, candles, config={**cfg, "_wf_mode": False})
    if isinstance(result, dict):
        result = dict(result)
        result["objective_kind"] = "val_holdout_fallback"
    return result if isinstance(result, dict) else {"ok": False, "error": "train failed"}


def _slice_candles_for_fidelity(candles: list, fraction: float) -> list:
    if not candles or fraction >= 0.999:
        return candles
    n = max(80, int(len(candles) * max(0.1, min(1.0, fraction))))
    return candles[-n:]


def _apply_fidelity_caps(params: dict, *, screen: bool) -> dict:
    out = dict(params)
    if not screen:
        return out
    # Opt #7: more aggressive screen fidelity (epochs/5, ~25% data via screen_fraction).
    if "epochs" in out:
        try:
            out["epochs"] = max(8, int(int(out["epochs"]) / 5))
        except (TypeError, ValueError):
            pass
    if "total_timesteps" in out:
        try:
            out["total_timesteps"] = max(2048, int(int(out["total_timesteps"]) / 5))
        except (TypeError, ValueError):
            pass
    if "gbm_max_iter" in out:
        try:
            out["gbm_max_iter"] = max(40, int(int(out["gbm_max_iter"]) / 5))
        except (TypeError, ValueError):
            pass
    # Only clamp early-stop when the trial actually searched / set it —
    # don't inject a default that overrides base config for GBM etc.
    if "early_stop_patience" in out:
        try:
            out["early_stop_patience"] = min(int(out["early_stop_patience"]), 8)
        except (TypeError, ValueError):
            pass
    # Avoid writing live champions during screen trials
    out["skip_persist"] = True
    out["skip_snapshot"] = True
    out["_hyperparam_screen"] = True
    return out


def _optuna_startup_workers(n_startup: int, strategy: str) -> int:
    """Parallel batch size for Optuna random startup trials only.

    Shipped default is 1 (sequential). Raise ``ML_OPTUNA_STARTUP_WORKERS`` to
    opt into parallel ask/tell for GBM screen startups only.
    """
    if n_startup <= 1:
        return 1
    # GPU/deep trains: keep startup sequential (VRAM contention).
    strat = str(strategy or "").upper()
    if strat != "ML_SIGNAL_BOOST":
        return 1
    try:
        from app.config import ML_OPTUNA_STARTUP_WORKERS

        cap = max(1, int(ML_OPTUNA_STARTUP_WORKERS or 1))
    except Exception:
        cap = 1
    return max(1, min(n_startup, cap, 4))


def run_ml_hyperparam_sweep(
    strategy: str,
    symbol: str,
    candles: list,
    *,
    config: dict | None = None,
    max_trials: int = 20,
    time_budget_sec: float = 600.0,
    patience: int = 8,
    multi_fidelity: bool = True,
    screen_fraction: float = 0.25,
    promote_top_k: int = 3,
    custom_search_space: dict | None = None,
    progress_cb: Callable[[dict], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    train_fn: Callable[..., dict] | None = None,
    objective_kind: str = "purged_cv",
    cv_folds_screen: int = 2,
    cv_folds_full: int = 3,
    job_id: str | None = None,
    resume_study_path: str | None = None,
) -> dict[str, Any]:
    """Run Optuna TPE sweep over ML training hyperparameters.

    Parameters
    ----------
    train_fn
        Optional override ``(symbol, candles, config=cfg) -> result``.
        Defaults to ``run_train_job``.
    objective_kind
        ``purged_cv`` (default) scores trials with purged walk-forward OOS;
        ``val_holdout`` uses a single train validation split (faster screen).
    job_id / resume_study_path
        When set, Optuna study is persisted under ``data/optuna/{job_id}.db``
        and trial history is flushed into ``ml_jobs.checkpoint_json`` so a
        restart can continue remaining trials.
    """
    strat = str(strategy or "").upper()
    if strat not in SWEEPABLE_ML_STRATEGIES:
        return {
            "ok": False,
            "error": f"Hyperparam sweep not supported for {strat}",
            "strategy": strat,
            "symbol": symbol,
        }
    if len(candles or []) < 120:
        return {
            "ok": False,
            "error": f"Need ≥120 candles for hyperparam sweep (got {len(candles or [])})",
            "strategy": strat,
            "symbol": symbol,
        }

    try:
        import optuna
        from optuna.samplers import TPESampler
    except ImportError as exc:
        return {
            "ok": False,
            "error": f"optuna required for hyperparam sweep: {exc}",
            "strategy": strat,
            "symbol": symbol,
        }

    if train_fn is None:
        from app.services.bots.ml_train_executor import run_train_job

        def train_fn(sym, bars, config=None):  # noqa: B023
            return run_train_job(strat, sym, bars, config)

    use_purged = str(objective_kind or "purged_cv").lower() in ("purged_cv", "purged", "cv", "wf")

    def _evaluate(bars: list, cfg: dict, *, folds: int, screen: bool) -> dict:
        # Screen can stay on cheap val holdout when multi-fidelity; promote always purged.
        if use_purged and (not screen or folds >= 2):
            # Screen with purged CV uses fewer folds + shorter series
            if screen:
                cfg = {**cfg, "hyperparam_cv_max_bars": int(cfg.get("hyperparam_cv_max_bars") or 1000)}
            return evaluate_trial_purged_cv(
                strat, symbol, bars, cfg,
                n_folds=folds, train_fn=train_fn,
            )
        return train_fn(symbol, bars, config=cfg)

    base_cfg = dict(config or {})
    # Allow config override
    if "objective_kind" in base_cfg:
        use_purged = str(base_cfg.get("objective_kind")).lower() in ("purged_cv", "purged", "cv", "wf")
    space = merge_search_space(strat, custom_search_space)
    if not space:
        return {"ok": False, "error": f"Empty search space for {strat}", "strategy": strat}

    max_trials = max(1, min(80, int(max_trials or 20)))
    time_budget_sec = max(30.0, float(time_budget_sec or 600.0))
    patience = max(2, min(30, int(patience or 8)))
    promote_top_k = max(1, min(10, int(promote_top_k or 3)))
    # Keep screen folds at ≥2 (deploy decision — do not drop to 1).
    cv_folds_screen = max(2, min(4, int(cv_folds_screen or 2)))
    cv_folds_full = max(2, min(5, int(cv_folds_full or 3)))
    n_startup = min(5, max_trials)

    from app.services.bots.ml_job_checkpoint import (
        merge_hyperparam_trial,
        optuna_study_path,
    )
    from app.services.bots.ml_job_store import load_ml_job_checkpoint, save_ml_job_checkpoint

    study_path = resume_study_path or base_cfg.get("resume_study_path")
    if not study_path and job_id:
        study_path = optuna_study_path(job_id)
    storage = None
    study_name = f"ml_hp_{job_id or 'anon'}"
    if study_path:
        storage = f"sqlite:///{study_path.replace(chr(92), '/')}"

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    try:
        if storage:
            study = optuna.create_study(
                study_name=study_name,
                storage=storage,
                load_if_exists=True,
                direction="maximize",
                sampler=TPESampler(seed=42, n_startup_trials=n_startup),
            )
        else:
            study = optuna.create_study(
                direction="maximize",
                sampler=TPESampler(seed=42, n_startup_trials=n_startup),
            )
    except Exception:
        logger.exception("Optuna study create/load failed — falling back to in-memory")
        study = optuna.create_study(
            direction="maximize",
            sampler=TPESampler(seed=42, n_startup_trials=n_startup),
        )
        study_path = None

    prior_cp = load_ml_job_checkpoint(job_id) if job_id else None
    prior_history = list((prior_cp or {}).get("trial_history") or []) if prior_cp else []
    completed_from_study = len([t for t in study.trials if t.state.name == "COMPLETE"])
    already_done = max(len(prior_history), completed_from_study)

    t0 = time.monotonic()
    trial_history: list[dict[str, Any]] = list(prior_history)
    best_score = -1e18
    if prior_cp and prior_cp.get("best_score") is not None:
        try:
            best_score = float(prior_cp.get("best_score"))
        except (TypeError, ValueError):
            best_score = -1e18
    no_improve = 0
    screen_rows: list[dict[str, Any]] = [r for r in trial_history if r.get("phase") != "full"]

    def _flush_checkpoint(row: dict | None = None, *, best_params: dict | None = None) -> None:
        if not job_id:
            return
        try:
            save_ml_job_checkpoint(
                job_id,
                merge_hyperparam_trial(
                    load_ml_job_checkpoint(job_id),
                    job_id=job_id,
                    strategy=strat,
                    symbol=symbol,
                    config=base_cfg,
                    max_trials=max_trials,
                    trial_row=row,
                    best_hyperparams=best_params,
                    best_score=None if best_score <= -1e8 else float(best_score),
                    study_path=study_path,
                ),
            )
        except Exception:
            logger.debug("hyperparam checkpoint flush failed", exc_info=True)

    # Seed empty checkpoint so hydrate sees resume_ok even before first trial.
    if job_id and already_done == 0 and not prior_cp:
        try:
            from app.services.bots.ml_job_checkpoint import empty_hyperparam_checkpoint
            save_ml_job_checkpoint(
                job_id,
                empty_hyperparam_checkpoint(
                    job_id=job_id, strategy=strat, symbol=symbol,
                    config=base_cfg, max_trials=max_trials,
                ),
            )
        except Exception:
            pass

    def _emit(payload: dict) -> None:
        if progress_cb:
            try:
                progress_cb(payload)
            except Exception:
                logger.debug("hyperparam progress_cb failed", exc_info=True)

    if already_done > 0:
        _emit({
            "pct": min(10 + int((already_done / max(max_trials, 1)) * 80), 90),
            "phase": "hyperparam_resume",
            "detail": f"Resuming trial {already_done}/{max_trials}…",
            "trial": already_done,
            "max_trials": max_trials,
            "trials_completed": already_done,
            "best_score": None if best_score <= -1e8 else round(float(best_score), 6),
            "resuming": True,
        })


    n_screen = max(1, int(max_trials * 0.6)) if multi_fidelity else max_trials
    n_screen = min(n_screen, max_trials)
    early_stopped = False

    def _record_screen_trial(
        i: int,
        trial,
        params: dict,
        result: dict,
    ) -> bool:
        """Tell Optuna + emit progress. Returns True if early-stop should break."""
        nonlocal best_score, no_improve, early_stopped
        score = extract_objective_score(result, strat)
        study.tell(trial, float(score))
        row = {
            "trial": i + 1,
            "phase": "screen" if multi_fidelity else "full",
            "params": params,
            "score": None if score <= -1e8 else round(float(score), 6),
            "ok": bool(result.get("ok")),
            "error": result.get("error"),
            "objective_kind": result.get("objective_kind") or ("purged_cv" if use_purged else "val_holdout"),
            "metrics": result.get("metrics") if isinstance(result.get("metrics"), dict) else {},
            "aggregate": result.get("aggregate") if isinstance(result.get("aggregate"), dict) else None,
        }
        screen_rows.append(row)
        trial_history.append(row)

        if score > best_score + 1e-9:
            best_score = score
            no_improve = 0
        else:
            no_improve += 1

        _flush_checkpoint(row, best_params=dict(params) if score >= best_score - 1e-12 else None)

        warn = _trial_warning(result, score=score)
        snap_metrics = _pick_progress_metrics(result)
        score_s = "—" if score <= -1e8 else str(round(float(score), 4))
        best_s = "—" if best_score <= -1e8 else str(round(float(best_score), 4))
        _emit({
            "pct": min(85, int(((i + 1) / max(max_trials, 1)) * 80) + 5),
            "phase": "hyperparam_screen" if multi_fidelity else "hyperparam_trial",
            "detail": (
                f"{'screen' if multi_fidelity else 'trial'} {i + 1}/{max_trials}"
                f" · score={score_s} · best={best_s}"
            ),
            "trial": i + 1,
            "max_trials": max_trials,
            "trials_completed": len(trial_history),
            "best_score": None if best_score <= -1e8 else round(float(best_score), 6),
            "last_score": None if score <= -1e8 else round(float(score), 6),
            "last_ok": bool(result.get("ok")),
            "fidelity_phase": "screen" if multi_fidelity else "full",
            "objective_kind": row.get("objective_kind"),
            "no_improve_streak": no_improve,
            "elapsed_sec": round(time.monotonic() - t0, 1),
            "multi_fidelity": bool(multi_fidelity),
            "metrics": snap_metrics,
            "warning": warn,
            "level": "warn" if warn else "info",
        })

        if no_improve >= patience and i + 1 >= min(5, max_trials):
            early_stopped = True
            _emit({
                "pct": min(85, int(((i + 1) / max(max_trials, 1)) * 80) + 5),
                "phase": "hyperparam_early_stop",
                "detail": f"early stop — no improve for {no_improve} trials",
                "trial": i + 1,
                "max_trials": max_trials,
                "trials_completed": len(trial_history),
                "best_score": None if best_score <= -1e8 else round(float(best_score), 6),
                "warning": f"early stop after {no_improve} non-improving trials",
                "level": "warn",
                "elapsed_sec": round(time.monotonic() - t0, 1),
            })
            return True
        return False

    def _run_one_screen(
        trial,
        params: dict,
        *,
        disable_wf_parallel: bool = False,
    ) -> dict:
        trial_cfg = {
            **base_cfg,
            **_apply_fidelity_caps(params, screen=multi_fidelity),
            "skip_persist": True,
            "skip_snapshot": True,
        }
        # Avoid nested ThreadPool (Optuna trial workers × WF fold workers).
        if disable_wf_parallel:
            trial_cfg["_disable_wf_fold_parallel"] = True
        bars = _slice_candles_for_fidelity(candles, screen_fraction if multi_fidelity else 1.0)
        try:
            is_screen = bool(multi_fidelity)
            return _evaluate(
                bars, trial_cfg,
                folds=(cv_folds_screen if is_screen else cv_folds_full) if use_purged else 1,
                screen=is_screen,
            )
        except Exception as exc:
            logger.exception("Hyperparam screen trial failed")
            return {"ok": False, "error": str(exc)}

    # ── Phase A: multi-fidelity screen (optional) ─────────────────────────
    # Opt #3: parallel ask/tell for random startup trials only (TPE still sequential after).
    # Skip trials already completed via Optuna storage / checkpoint resume.
    i = int(already_done)
    startup_parallel = _optuna_startup_workers(min(n_startup, max(0, n_screen - i)), strat)
    if startup_parallel > 1 and i < min(n_startup, n_screen):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        batch_n = min(startup_parallel, max(0, n_startup - i), max(0, n_screen - i))
        batch: list[tuple[int, Any, dict]] = []
        for _ in range(max(0, batch_n)):
            if cancel_cb and cancel_cb():
                break
            if time.monotonic() - t0 >= time_budget_sec:
                break
            trial = study.ask()
            params = _suggest_from_space(trial, space)
            batch.append((i, trial, params))
            i += 1
        if batch:
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                futs = {
                    pool.submit(
                        _run_one_screen, trial, params, disable_wf_parallel=True,
                    ): (idx, trial, params)
                    for idx, trial, params in batch
                }
                completed: list[tuple[int, Any, dict, dict]] = []
                for fut in as_completed(futs):
                    idx, trial, params = futs[fut]
                    try:
                        result = fut.result()
                    except Exception as exc:
                        result = {"ok": False, "error": str(exc)}
                    completed.append((idx, trial, params, result))
                # Tell *all* completed trials in order — never leave Optuna
                # trials stuck RUNNING when early-stop triggers mid-batch.
                for idx, trial, params, result in sorted(completed, key=lambda t: t[0]):
                    _record_screen_trial(idx, trial, params, result)

    while i < n_screen and not early_stopped:
        if cancel_cb and cancel_cb():
            break
        if time.monotonic() - t0 >= time_budget_sec:
            break

        trial = study.ask()
        params = _suggest_from_space(trial, space)
        result = _run_one_screen(trial, params)
        if _record_screen_trial(i, trial, params, result):
            break
        i += 1

    # ── Phase B: promote top-k to full training ────────────────────────────
    promoted: list[dict[str, Any]] = []
    if multi_fidelity and screen_rows:
        ranked = sorted(
            [r for r in screen_rows if r.get("score") is not None],
            key=lambda r: float(r["score"]),
            reverse=True,
        )[:promote_top_k]
        for j, row in enumerate(ranked):
            if cancel_cb and cancel_cb():
                break
            if time.monotonic() - t0 >= time_budget_sec:
                break
            params = dict(row.get("params") or {})
            trial_cfg = {
                **base_cfg,
                **params,
                # Final promote may persist champion when caller allows
                "skip_persist": bool(base_cfg.get("skip_persist", False)),
                "skip_snapshot": bool(base_cfg.get("skip_snapshot", False)),
            }
            try:
                result = _evaluate(
                    candles, trial_cfg,
                    folds=cv_folds_full,
                    screen=False,
                )
            except Exception as exc:
                logger.exception("Hyperparam promote trial failed")
                result = {"ok": False, "error": str(exc)}
            score = extract_objective_score(result, strat)
            full_row = {
                "trial": len(trial_history) + 1,
                "phase": "full",
                "params": params,
                "score": None if score <= -1e8 else round(float(score), 6),
                "ok": bool(result.get("ok")),
                "error": result.get("error"),
                "objective_kind": result.get("objective_kind") or ("purged_cv" if use_purged else "val_holdout"),
                "metrics": result.get("metrics") if isinstance(result.get("metrics"), dict) else {},
                "aggregate": result.get("aggregate") if isinstance(result.get("aggregate"), dict) else None,
                "promoted_from": row.get("trial"),
            }
            promoted.append(full_row)
            trial_history.append(full_row)
            if score > best_score + 1e-9:
                best_score = score
            _flush_checkpoint(full_row, best_params=dict(params) if score >= best_score - 1e-12 else None)
            warn = _trial_warning(result, score=score)
            score_s = "—" if score <= -1e8 else str(round(float(score), 4))
            best_s = "—" if best_score <= -1e8 else str(round(float(best_score), 4))
            _emit({
                "pct": min(95, 85 + int(((j + 1) / max(len(ranked), 1)) * 10)),
                "phase": "hyperparam_promote",
                "detail": (
                    f"full train {j + 1}/{len(ranked)}"
                    f" · score={score_s} · best={best_s}"
                ),
                "trial": full_row["trial"],
                "max_trials": max_trials,
                "trials_completed": len(trial_history),
                "promote_index": j + 1,
                "promote_total": len(ranked),
                "promoted_from": row.get("trial"),
                "best_score": None if best_score <= -1e8 else round(float(best_score), 6),
                "last_score": None if score <= -1e8 else round(float(score), 6),
                "last_ok": bool(result.get("ok")),
                "fidelity_phase": "full",
                "objective_kind": full_row.get("objective_kind"),
                "elapsed_sec": round(time.monotonic() - t0, 1),
                "multi_fidelity": True,
                "metrics": _pick_progress_metrics(result),
                "warning": warn,
                "level": "warn" if warn else "info",
            })

    # Prefer best full-phase row; else best screen row
    candidates = [r for r in trial_history if r.get("score") is not None]
    full_ok = [r for r in candidates if r.get("phase") == "full" and r.get("ok")]
    pool = full_ok or candidates
    best_row = max(pool, key=lambda r: float(r["score"])) if pool else None
    best_hyperparams = dict(best_row.get("params") or {}) if best_row else {}

    importance: dict[str, float] = {}
    try:
        if len(study.trials) >= 3:
            raw_imp = optuna.importance.get_param_importances(study)
            importance = {k: round(float(v), 4) for k, v in raw_imp.items()}
    except Exception:
        logger.debug("param importance unavailable", exc_info=True)

    elapsed = time.monotonic() - t0
    return {
        "ok": True,
        "strategy": strat,
        "symbol": symbol,
        "best_hyperparams": best_hyperparams,
        "best_score": None if best_score <= -1e8 else round(float(best_score), 6),
        "trial_history": trial_history,
        "importance_ranking": importance,
        "trials_completed": len(trial_history),
        "max_trials": max_trials,
        "elapsed_sec": round(elapsed, 2),
        "multi_fidelity": bool(multi_fidelity),
        "objective_kind": "purged_cv" if use_purged else "val_holdout",
        "search_space": space,
        "promoted": len(promoted),
        "convergence": [
            {"trial": r["trial"], "score": r.get("score"), "phase": r.get("phase")}
            for r in trial_history
            if r.get("score") is not None
        ],
    }
