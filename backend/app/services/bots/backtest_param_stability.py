"""Parameter stability — plateau detection and centroid selection."""

from __future__ import annotations

import copy
import statistics
from typing import Any

from app.services.bots.backtest_walk_forward import (
    row_objective_value,
    sort_sweep_rows,
)


def _numeric_values(values: list[Any]) -> list[float]:
    out: list[float] = []
    for v in values:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def compute_centroid_config(
    rows: list[dict],
    *,
    axes: list[tuple[str, list[Any]]] | None = None,
) -> dict | None:
    """Mean/mode config across top-performing sweep rows."""
    if not rows:
        return None
    keys = [k for k, _ in (axes or [])]
    if not keys:
        keys = sorted({
            k for row in rows
            for k in (row.get("config") or {})
            if k not in ("sim_mode", "live_parity", "score_from_time")
        })
    centroid: dict[str, Any] = {}
    for key in keys:
        vals = [(row.get("config") or {}).get(key) for row in rows if (row.get("config") or {}).get(key) is not None]
        if not vals:
            continue
        nums = _numeric_values(vals)
        if nums and len(nums) == len(vals):
            mean = sum(nums) / len(nums)
            if all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
                centroid[key] = int(round(mean))
            else:
                centroid[key] = round(mean, 4)
        else:
            centroid[key] = max(set(vals), key=vals.count)
    return centroid or copy.deepcopy(rows[0].get("config"))


def analyze_parameter_stability(
    sweep_rows: list[dict],
    *,
    objective: str = "total_pnl",
    min_trades: int = 0,
    top_fraction: float = 0.25,
    axes: list[tuple[str, list[Any]]] | None = None,
) -> dict[str, Any]:
    """
    Rank configs by objective spread in the top quartile and recommend a stable pick.

    Prefers centroid of top quartile over a single sharp peak when spread is high.
    """
    ranked = sort_sweep_rows(sweep_rows, objective=objective, min_trades=min_trades)
    if not ranked:
        return {
            "stable_pick": None,
            "centroid_config": None,
            "best_config": None,
            "recommendation": "none",
            "top_quartile_count": 0,
        }

    top_k = max(1, int(len(ranked) * top_fraction))
    top_rows = ranked[:top_k]
    scores = [row_objective_value(r, objective) for r in top_rows]
    score_spread = (max(scores) - min(scores)) if scores else 0.0
    score_std = statistics.pstdev(scores) if len(scores) > 1 else 0.0

    param_variance: dict[str, float] = {}
    if axes:
        for key, _ in axes:
            nums = _numeric_values([(r.get("config") or {}).get(key) for r in top_rows])
            if len(nums) >= 2:
                param_variance[key] = round(statistics.pstdev(nums), 4)

    centroid = compute_centroid_config(top_rows, axes=axes)
    best = ranked[0].get("config")
    stable_pick = centroid
    recommendation = "centroid"
    if score_spread < 1e-6 or (scores and (scores[0] - (scores[-1] if len(scores) > 1 else scores[0])) < 0.05 * abs(scores[0] or 1)):
        stable_pick = best
        recommendation = "best_peak"

    return {
        "best_config": best,
        "centroid_config": centroid,
        "stable_pick": stable_pick,
        "recommendation": recommendation,
        "top_quartile_count": top_k,
        "objective_spread": round(score_spread, 4),
        "objective_std": round(score_std, 4),
        "param_variance": param_variance,
        "plateau_detected": score_std < 0.1 * abs(scores[0]) if scores and scores[0] else False,
    }
