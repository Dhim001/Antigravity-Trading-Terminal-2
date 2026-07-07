"""Bayesian parameter search (Optuna TPE) for backtest optimization."""

from __future__ import annotations

import copy
from typing import Any, Callable

from app.services.bots.backtest_sweep import (
    SWEEP_MODES,
    _build_axes,
    _max_combos_for_mode,
    sweep_label,
)
from app.services.bots.backtest_walk_forward import (
    row_objective_value,
    row_trade_count,
    sort_sweep_rows,
)


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
    return {
        "label": sweep_label(cfg),
        "config": cfg,
        "summary": res.get("summary") or {},
        "total_pnl": res.get("total_pnl"),
        "trade_count": res.get("trade_count"),
        "trial": trial_number,
    }


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
    Sequential TPE search with early stopping when objective plateaus.

    Returns (sweep_rows, study_meta).
    """
    try:
        import optuna
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
    study = optuna.create_study(direction="maximize", sampler=sampler)

    rows: list[dict] = []
    best_score = -1e18
    no_improve = 0
    early_stopped = False

    for trial_idx in range(n_trials):
        if cancel_cb and cancel_cb():
            break
        if budget_tracker.should_stop() and trial_idx > 0:
            early_stopped = True
            break

        trial = study.ask()
        cfg = _suggest_config(trial, base_config, axes)
        res = evaluate_fn(cfg)
        if res.get("cancelled"):
            break

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
        **budget_meta,
    }
    return sort_sweep_rows(rows, objective=objective, min_trades=min_trades), meta


def ensure_bayesian_mode_registered() -> None:
    """No-op — bayesian is registered in SWEEP_MODES."""
    return None
