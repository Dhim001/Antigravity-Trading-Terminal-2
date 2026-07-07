"""Probability of Backtest Overfitting (PBO) via Combinatorial Symmetric CV."""

from __future__ import annotations

import json
import math
from itertools import combinations
from typing import Any, Callable

from app.services.bots.backtest_purged_cv import partition_candles
from app.services.bots.backtest_walk_forward import row_objective_value, sort_sweep_rows


def _config_key(cfg: dict) -> str:
    return json.dumps(cfg, sort_keys=True, default=str)


def compute_pbo_from_matrix(
    performance_matrix: list[list[float]],
    *,
    n_test_groups: int | None = None,
) -> dict[str, Any]:
    """
    CSCV-lite PBO from segment × strategy performance matrix.

    Rows = time groups, cols = strategies. For each symmetric split, rank strategies
    on IS groups; count when the IS winner ranks in the bottom half on OOS.
    """
    if not performance_matrix or not performance_matrix[0]:
        return {"pbo": None, "note": "Empty performance matrix"}

    n_groups = len(performance_matrix)
    n_strategies = len(performance_matrix[0])
    if n_groups < 4 or n_strategies < 2:
        return {
            "pbo": None,
            "note": "Need >= 4 time groups and >= 2 strategies",
            "groups": n_groups,
            "strategies": n_strategies,
        }

    n_test = n_test_groups or n_groups // 2
    n_test = max(1, min(n_test, n_groups - 1))

    n_splits = 0
    n_overfit = 0
    logit_ranks: list[float] = []

    for test_indices in combinations(range(n_groups), n_test):
        train_indices = [i for i in range(n_groups) if i not in test_indices]
        if not train_indices:
            continue

        is_means = []
        oos_means = []
        for s in range(n_strategies):
            is_vals = [performance_matrix[g][s] for g in train_indices]
            oos_vals = [performance_matrix[g][s] for g in test_indices]
            is_means.append(sum(is_vals) / len(is_vals))
            oos_means.append(sum(oos_vals) / len(oos_vals))

        best_is = max(range(n_strategies), key=lambda i: is_means[i])
        sorted_oos = sorted(
            range(n_strategies),
            key=lambda i: oos_means[i],
            reverse=True,
        )
        oos_rank = sorted_oos.index(best_is)
        relative_rank = oos_rank / max(n_strategies - 1, 1)
        if relative_rank > 0.5:
            n_overfit += 1
        n_splits += 1
        if 0 < relative_rank < 1:
            logit_ranks.append(math.log(relative_rank / (1.0 - relative_rank)))
        elif relative_rank <= 0:
            logit_ranks.append(4.0)
        else:
            logit_ranks.append(-4.0)

    pbo = (n_overfit / n_splits) if n_splits else None
    risk = "low"
    if pbo is not None:
        if pbo >= 0.5:
            risk = "high"
        elif pbo >= 0.35:
            risk = "moderate"

    return {
        "pbo": round(pbo, 4) if pbo is not None else None,
        "splits_evaluated": n_splits,
        "overfit_splits": n_overfit,
        "groups": n_groups,
        "strategies": n_strategies,
        "n_test_groups": n_test,
        "risk_label": risk,
        "mean_logit_rank": round(sum(logit_ranks) / len(logit_ranks), 4) if logit_ranks else None,
    }


def run_pbo_audit(
    *,
    sweep_rows: list[dict],
    candles: list[dict],
    evaluate_fn: Callable[[dict, list[dict]], dict],
    objective: str = "total_pnl",
    min_trades: int = 0,
    top_k: int = 8,
    n_groups: int = 8,
    cancel_cb: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Build segment performance matrix for top-K configs and compute PBO."""
    ranked = sort_sweep_rows(sweep_rows, objective=objective, min_trades=min_trades)
    if not ranked:
        return {"pbo": None, "note": "No eligible sweep rows"}

    top = ranked[: max(3, min(int(top_k), len(ranked)))]
    segments = partition_candles(candles, n_groups)
    if len(segments) < 4:
        return {"pbo": None, "note": "Not enough bars for CSCV groups", "segments": len(segments)}

    matrix: list[list[float]] = []
    for seg in segments:
        if cancel_cb and cancel_cb():
            return {"pbo": None, "cancelled": True}
        row: list[float] = []
        for entry in top:
            cfg = entry.get("config") or {}
            res = evaluate_fn(cfg, seg)
            if res.get("error"):
                row.append(-1e18)
            else:
                row.append(
                    row_objective_value(
                        {
                            "total_pnl": res.get("total_pnl"),
                            "summary": res.get("summary") or {},
                            "trade_count": res.get("trade_count"),
                            "config": cfg,
                        },
                        objective,
                    )
                )
        matrix.append(row)

    result = compute_pbo_from_matrix(matrix, n_test_groups=len(segments) // 2)
    result["configs_audited"] = len(top)
    result["objective"] = objective
    return result
