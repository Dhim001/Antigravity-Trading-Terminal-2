"""Multi-objective ranking and Pareto frontier for sweep results."""

from __future__ import annotations

import math
from typing import Any

from app.services.bots.backtest_walk_forward import row_objective_value, row_trade_count


def robust_score(
    row: dict,
    *,
    stability_factor: float = 1.0,
) -> float:
    """Composite: Sharpe × sqrt(trades) × stability — favors robust configs."""
    summary = row.get("summary") or {}
    sharpe = summary.get("sharpe_ratio")
    if sharpe is None:
        sharpe = row_objective_value(row, "sharpe_ratio")
    if sharpe is None or float(sharpe) <= -1e17:
        return -1e18
    trades = row_trade_count(row)
    if trades <= 0:
        return -1e18
    stab = max(0.1, min(1.0, float(stability_factor)))
    return float(sharpe) * math.sqrt(min(trades, 100)) * stab


def stress_pnl_value(row: dict) -> float:
    """PnL after doubling estimated slippage cost (stress scenario)."""
    summary = row.get("summary") or {}
    pnl = float(row.get("total_pnl") or summary.get("total_pnl") or 0)
    fees = float(summary.get("total_fees") or 0)
    trades = row_trade_count(row)
    slip_bps = float(summary.get("slippage_bps") or (row.get("config") or {}).get("slippage_bps") or 5)
    alloc = float((row.get("config") or {}).get("allocation") or 10_000)
    extra_slip = trades * alloc * (slip_bps / 10_000.0)
    return pnl - fees - extra_slip


def _extract_metric(row: dict, metric: str) -> float | None:
    summary = row.get("summary") or {}
    if metric == "total_pnl":
        val = row.get("total_pnl") if row.get("total_pnl") is not None else summary.get("total_pnl")
    elif metric == "max_drawdown":
        val = summary.get("max_drawdown")
    elif metric == "trade_count":
        val = row_trade_count(row)
    elif metric == "sharpe_ratio":
        val = summary.get("sharpe_ratio")
    else:
        val = summary.get(metric) if metric in summary else row.get(metric)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _dominates(a: dict, b: dict, objectives: list[tuple[str, bool]]) -> bool:
    """True if a Pareto-dominates b (maximize or minimize per objective)."""
    better_strict = False
    for metric, maximize in objectives:
        av = _extract_metric(a, metric)
        bv = _extract_metric(b, metric)
        if av is None or bv is None:
            continue
        if maximize:
            if av < bv:
                return False
            if av > bv:
                better_strict = True
        else:
            if av > bv:
                return False
            if av < bv:
                better_strict = True
    return better_strict


def pareto_frontier(
    rows: list[dict],
    *,
    objectives: list[tuple[str, bool]] | None = None,
    max_points: int = 8,
) -> list[dict]:
    """
    Non-dominated configs for multi-objective comparison.

    Default objectives: maximize PnL, minimize max_drawdown, maximize trade_count.
    """
    objs = objectives or [
        ("total_pnl", True),
        ("max_drawdown", False),
        ("trade_count", True),
    ]
    eligible = [r for r in rows if not r.get("error")]
    frontier: list[dict] = []
    for row in eligible:
        dominated = False
        for other in eligible:
            if other is row:
                continue
            if _dominates(other, row, objs):
                dominated = True
                break
        if not dominated:
            frontier.append(row)
    ranked = sorted(
        frontier,
        key=lambda r: (
            _extract_metric(r, "total_pnl") or -1e18,
            -(_extract_metric(r, "max_drawdown") or 1e18),
        ),
        reverse=True,
    )
    return ranked[:max_points]
