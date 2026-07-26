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
    out = ranked[:max_points]
    # Annotate crowding distance for UI ranking
    distances = crowding_distance(out, objs)
    for i, row in enumerate(out):
        row = dict(row)
        row["crowding_distance"] = distances[i] if i < len(distances) else 0.0
        out[i] = row
    return out


def crowding_distance(
    frontier: list[dict],
    objectives: list[tuple[str, bool]] | None = None,
) -> list[float]:
    """NSGA-II style crowding distance for Pareto frontier rows."""
    objs = objectives or [
        ("total_pnl", True),
        ("max_drawdown", False),
        ("trade_count", True),
    ]
    n = len(frontier)
    if n == 0:
        return []
    if n <= 2:
        return [float("inf")] * n
    dist = [0.0] * n
    for metric, _maximize in objs:
        order = sorted(
            range(n),
            key=lambda i: _extract_metric(frontier[i], metric) if _extract_metric(frontier[i], metric) is not None else 0.0,
        )
        dist[order[0]] = float("inf")
        dist[order[-1]] = float("inf")
        vals = [_extract_metric(frontier[i], metric) for i in order]
        lo = next((v for v in vals if v is not None), 0.0)
        hi = next((v for v in reversed(vals) if v is not None), 0.0)
        span = (hi - lo) if hi != lo else 1.0
        for k in range(1, n - 1):
            prev_v = vals[k - 1]
            next_v = vals[k + 1]
            if prev_v is None or next_v is None:
                continue
            if dist[order[k]] != float("inf"):
                dist[order[k]] += (next_v - prev_v) / span
    return dist


def run_nsga2_selection(
    rows: list[dict],
    *,
    objectives: list[tuple[str, bool]] | None = None,
    population: int = 16,
) -> list[dict]:
    """Rank rows by non-domination + crowding (NSGA-II selection flavor).

    Used when ``multi_objective_sampler=nsga2`` is set on a sweep — filters
    the evaluated population down to a diverse Pareto-aware elite set.
    """
    objs = objectives or [
        ("total_pnl", True),
        ("max_drawdown", False),
        ("sharpe_ratio", True),
    ]
    eligible = [r for r in rows if not r.get("error")]
    if not eligible:
        return []

    def _front(pool: list[dict]) -> list[dict]:
        """Non-dominated subset preserving object identity (no copies)."""
        out: list[dict] = []
        for row in pool:
            dominated = False
            for other in pool:
                if other is row:
                    continue
                if _dominates(other, row, objs):
                    dominated = True
                    break
            if not dominated:
                out.append(row)
        return out

    # Rank 0 = current Pareto, then peel layers
    remaining = list(eligible)
    ranked: list[dict] = []
    guard = 0
    while remaining and len(ranked) < population and guard < len(eligible) + 2:
        guard += 1
        layer = _front(remaining)
        if not layer:
            break
        cds = crowding_distance(layer, objs)
        order = sorted(range(len(layer)), key=lambda i: cds[i], reverse=True)
        for i in order:
            if len(ranked) >= population:
                break
            row = dict(layer[i])
            row["nsga_rank"] = len(ranked)
            row["crowding_distance"] = cds[i]
            ranked.append(row)
        layer_ids = {id(r) for r in layer}
        remaining = [r for r in remaining if id(r) not in layer_ids]
    return ranked
