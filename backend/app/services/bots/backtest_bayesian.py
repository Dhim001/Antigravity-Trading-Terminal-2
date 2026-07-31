"""Bayesian parameter search (Optuna TPE) for backtest optimization."""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable

from app.services.bots.backtest_sweep import (
    _build_axes,
    _max_combos_for_mode,
    sweep_label,
)
from app.services.bots.backtest_walk_forward import (
    row_objective_value,
    row_trade_count,
    sort_sweep_rows,
)

logger = logging.getLogger(__name__)


def is_bayesian_sweep(sweep: dict | None) -> bool:
    mode = str((sweep or {}).get("sweep_mode") or "grid").lower()
    return mode == "bayesian"


def _suggest_config(trial, base_config: dict, axes: list[tuple[str, list[Any]]]) -> dict:
    cfg = copy.deepcopy(base_config or {})
    for key, vals in axes:
        if not vals:
            continue
        if all(isinstance(v, bool) for v in vals):
            cfg[key] = trial.suggest_categorical(key, vals)
        elif all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
            cfg[key] = trial.suggest_categorical(key, vals)
        elif all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            lo = float(min(vals))
            hi = float(max(vals))
            if lo == hi:
                cfg[key] = vals[0]
            else:
                cfg[key] = trial.suggest_float(key, lo, hi)
        else:
            cfg[key] = trial.suggest_categorical(key, vals)
    return cfg


def _result_to_row(cfg: dict, res: dict, *, trial_number: int) -> dict:
    if res.get("error"):
        return {
            "label": sweep_label(cfg),
            "config": cfg,
            "error": res["error"],
            "trial": trial_number,
        }
    from app.services.bots.backtest_walk_forward import slim_ml_metrics_for_sweep

    row = {
        "label": sweep_label(cfg),
        "config": cfg,
        "summary": res.get("summary") or {},
        "total_pnl": res.get("total_pnl"),
        "trade_count": res.get("trade_count"),
        "trial": trial_number,
    }
    slim = slim_ml_metrics_for_sweep(res.get("ml_metrics"))
    if slim:
        row["ml_metrics"] = slim
    return row


def _warm_start_study(study, sweep: dict, axes: list[tuple[str, list[Any]]], base_config: dict) -> int:
    """Enqueue prior best configs as completed trials. Returns count enqueued."""
    run_id = sweep.get("bayesian_warm_start_run_id") or sweep.get("warm_start_run_id")
    if not run_id:
        return 0
    try:
        from app.services.bots.optimization_store import get_optimization_run

        run = get_optimization_run(str(run_id))
    except Exception:
        logger.debug("warm-start load failed", exc_info=True)
        return 0
    if not run:
        return 0

    axis_keys = {k for k, _ in axes}
    seeds: list[dict] = []
    best = run.get("best_config")
    if isinstance(best, dict) and best:
        seeds.append(best)
    results = run.get("results") if isinstance(run.get("results"), list) else []
    for row in results[:12]:
        if not isinstance(row, dict):
            continue
        cfg = row.get("config") or row.get("params")
        if isinstance(cfg, dict):
            seeds.append(cfg)

    enqueued = 0
    seen: set[tuple] = set()
    for cfg in seeds:
        key = tuple(sorted((k, repr(cfg.get(k))) for k in axis_keys if k in cfg))
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            import optuna

            distributions = {}
            params = {}
            for ax_key, vals in axes:
                if ax_key not in cfg or not vals:
                    continue
                val = cfg[ax_key]
                if all(isinstance(v, bool) for v in vals):
                    distributions[ax_key] = optuna.distributions.CategoricalDistribution(vals)
                    params[ax_key] = bool(val) if isinstance(val, bool) else val
                elif all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
                    if val not in vals:
                        # snap to nearest
                        val = min(vals, key=lambda x: abs(x - int(val)))
                    distributions[ax_key] = optuna.distributions.CategoricalDistribution(vals)
                    params[ax_key] = int(val)
                elif all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
                    lo, hi = float(min(vals)), float(max(vals))
                    distributions[ax_key] = optuna.distributions.FloatDistribution(lo, hi)
                    params[ax_key] = float(max(lo, min(hi, float(val))))
                else:
                    if val not in vals:
                        continue
                    distributions[ax_key] = optuna.distributions.CategoricalDistribution(vals)
                    params[ax_key] = val
            if not params:
                continue
            score = -1e18
            # Prefer stored objective if present on matching result row. Seeds
            # come from row["config"] OR row["params"] — match both, otherwise
            # params-sourced seeds never find their score and get dropped.
            for row in results:
                if isinstance(row, dict) and (row.get("config") == cfg or row.get("params") == cfg):
                    try:
                        score = float(row_objective_value(row, "total_pnl") or -1e18)
                    except (TypeError, ValueError):
                        score = -1e18
                    break
            # Optuna rejects COMPLETE trials without a finite value — skip unusable seeds
            if score <= -1e17:
                continue
            trial = optuna.trial.create_trial(
                params=params,
                distributions=distributions,
                value=float(score),
                state=optuna.trial.TrialState.COMPLETE,
            )
            study.add_trial(trial)
            enqueued += 1
        except Exception:
            logger.debug("warm-start trial enqueue skipped", exc_info=True)
    return enqueued


def run_bayesian_sweep(
    *,
    base_config: dict,
    sweep: dict | None,
    evaluate_fn: Callable[[dict], dict],
    objective: str = "total_pnl",
    min_trades: int = 0,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    budget_tracker: Any | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """
    Sequential TPE search with MedianPruner, optional warm-start, and importance.

    Returns (sweep_rows, study_meta).
    """
    try:
        import optuna
        from optuna.pruners import MedianPruner
        from optuna.samplers import TPESampler
    except ImportError as exc:
        raise RuntimeError(
            "Bayesian sweep requires optuna — install with: pip install optuna>=3.0"
        ) from exc

    sweep = sweep or {}
    axes = _build_axes(base_config, sweep)
    if not axes:
        res = evaluate_fn(copy.deepcopy(base_config or {}))
        return [_result_to_row(base_config or {}, res, trial_number=0)], {
            "sweep_mode": "bayesian",
            "trials_completed": 1,
            "early_stopped": False,
            "note": "No sweep axes — single baseline run",
        }

    n_trials = _max_combos_for_mode(sweep, "bayesian")
    patience = max(3, int(sweep.get("bayesian_patience") or 12))
    n_startup = max(2, min(int(sweep.get("bayesian_startup_trials") or 8), n_trials // 2))
    seed_raw = sweep.get("sweep_seed")
    seed = int(seed_raw) if seed_raw is not None else None

    if budget_tracker is None:
        from app.services.bots.backtest_trial_budget import TrialBudgetTracker
        budget_tracker = TrialBudgetTracker(sweep)
    n_trials = min(n_trials, budget_tracker.max_trials)

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    sampler = TPESampler(seed=seed, n_startup_trials=n_startup)
    use_pruner = str(sweep.get("bayesian_pruner") or "median").lower() != "none"
    pruner = MedianPruner(n_startup_trials=max(2, n_startup // 2), n_warmup_steps=0) if use_pruner else optuna.pruners.NopPruner()
    study = optuna.create_study(direction="maximize", sampler=sampler, pruner=pruner)

    warm_started = _warm_start_study(study, sweep, axes, base_config or {})

    rows: list[dict] = []
    best_score = -1e18
    no_improve = 0
    early_stopped = False
    pruned_count = 0

    for trial_idx in range(n_trials):
        if cancel_cb and cancel_cb():
            break
        if budget_tracker.should_stop() and trial_idx > 0:
            early_stopped = True
            break

        trial = study.ask()
        cfg = _suggest_config(trial, base_config, axes)

        # Mid-trial prune hook: evaluate once; if caller returns prune_score at step 0
        # with equity_frac, report intermediate. Full evaluate remains primary path.
        res = evaluate_fn(cfg)
        if res.get("cancelled"):
            break

        if res.get("pruned") or res.get("should_prune"):
            study.tell(trial, state=optuna.trial.TrialState.PRUNED)
            pruned_count += 1
            row = _result_to_row(cfg, {"error": "pruned"}, trial_number=trial_idx + 1)
            row["pruned"] = True
            rows.append(row)
            budget_tracker.record_trial()
            if progress_cb:
                progress_cb(trial_idx + 1, n_trials)
            continue

        # Soft mid-run prune: if evaluate_fn embeds intermediate equity_pct_50
        mid = res.get("equity_pct_50")
        if use_pruner and mid is not None:
            try:
                trial.report(float(mid), step=1)
                if trial.should_prune():
                    study.tell(trial, state=optuna.trial.TrialState.PRUNED)
                    pruned_count += 1
                    row = _result_to_row(cfg, res, trial_number=trial_idx + 1)
                    row["pruned"] = True
                    rows.append(row)
                    budget_tracker.record_trial()
                    if progress_cb:
                        progress_cb(trial_idx + 1, n_trials)
                    continue
            except Exception:
                pass

        row = _result_to_row(cfg, res, trial_number=trial_idx + 1)
        rows.append(row)
        budget_tracker.record_trial()

        if res.get("error"):
            study.tell(trial, -1e18)
        else:
            trades = row_trade_count(row)
            score = (
                row_objective_value(row, objective)
                if trades >= max(0, int(min_trades or 0))
                else -1e18
            )
            study.tell(trial, float(score))
            if score > best_score + 1e-9:
                best_score = score
                no_improve = 0
            else:
                no_improve += 1

        if progress_cb:
            progress_cb(trial_idx + 1, n_trials)

        if no_improve >= patience and trial_idx + 1 >= n_startup:
            early_stopped = True
            break

        if budget_tracker.should_stop():
            early_stopped = True
            break

    importance: dict[str, float] = {}
    try:
        completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if len(completed) >= 3:
            raw = optuna.importance.get_param_importances(study)
            importance = {k: round(float(v), 4) for k, v in raw.items()}
    except Exception:
        logger.debug("bayesian param importance unavailable", exc_info=True)

    budget_meta = budget_tracker.to_meta()
    meta = {
        "sweep_mode": "bayesian",
        "trials_completed": len(rows),
        "trials_budget": n_trials,
        "early_stopped": early_stopped,
        "patience": patience,
        "startup_trials": n_startup,
        "best_value": round(best_score, 4) if best_score > -1e17 else None,
        "sampler": "TPE",
        "pruner": "MedianPruner" if use_pruner else "none",
        "pruned_trials": pruned_count,
        "warm_started_trials": warm_started,
        "hyperparameter_importance": importance,
        "importance_ranking": importance,
        "converged": bool(early_stopped and best_score > -1e17 and no_improve >= patience),
        **budget_meta,
    }
    return sort_sweep_rows(rows, objective=objective, min_trades=min_trades), meta


def ensure_bayesian_mode_registered() -> None:
    """No-op — bayesian is registered in SWEEP_MODES."""
    return None
