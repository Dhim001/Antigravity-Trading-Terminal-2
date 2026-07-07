"""Attach stability, Pareto, and Bayesian metadata to sweep results."""

from __future__ import annotations

from typing import Any

from app.services.bots.backtest_multi_objective import pareto_frontier
from app.services.bots.backtest_param_stability import analyze_parameter_stability
from app.services.bots.backtest_sweep import _build_axes
from app.services.bots.backtest_walk_forward import pick_best_config, sort_sweep_rows


def enrich_sweep_results(
    sweep_rows: list[dict],
    *,
    sweep: dict | None,
    base_config: dict,
    objective: str,
    min_trades: int,
    bayesian_meta: dict | None = None,
    trial_budget_meta: dict | None = None,
) -> dict[str, Any]:
    """Build sweep summary block with stability + multi-objective extras."""
    axes = _build_axes(base_config, sweep or {})
    ranked = sort_sweep_rows(sweep_rows, objective=objective, min_trades=min_trades)
    stability = analyze_parameter_stability(
        sweep_rows,
        objective=objective,
        min_trades=min_trades,
        axes=axes,
    )
    pareto = pareto_frontier(ranked)
    best_config, best_row = pick_best_config(sweep_rows, objective=objective, min_trades=min_trades)
    stable_config = stability.get("stable_pick") or best_config

    return {
        "configs_tested": len(sweep_rows),
        "best_config": best_config,
        "stable_config": stable_config,
        "best": best_row,
        "objective": objective,
        "min_trades": min_trades,
        "results": ranked,
        "stability": stability,
        "pareto_frontier": [
            {
                "label": r.get("label"),
                "config": r.get("config"),
                "total_pnl": r.get("total_pnl"),
                "summary": r.get("summary") or {},
            }
            for r in pareto
        ],
        "bayesian": bayesian_meta,
        "trial_budget": trial_budget_meta,
    }
