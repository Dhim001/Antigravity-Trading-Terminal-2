"""Monte Carlo bootstrap confidence bands from closed trade PnLs."""

from __future__ import annotations

import random
from typing import Any


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(pct * (len(sorted_vals) - 1))
    return sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]


def _downsample_fan(fan: list[dict], max_points: int = 40) -> list[dict]:
    if len(fan) <= max_points:
        return fan
    step = max(1, (len(fan) + max_points - 1) // max_points)
    out = [fan[i] for i in range(0, len(fan), step)]
    if out[-1] is not fan[-1]:
        out.append(fan[-1])
    return out


def monte_carlo_trade_bands(
    trades: list[dict],
    *,
    starting_equity: float = 10_000.0,
    simulations: int = 500,
    seed: int = 42,
) -> dict[str, Any] | None:
    """
    Resample closed-trade PnLs with replacement to estimate total PnL distribution.
    Returns 5th/50th/95th percentile total PnL and return %, plus fan bands.
    """
    closed = [t for t in (trades or []) if t.get("is_exit") and t.get("pnl") is not None]
    if len(closed) < 2:
        return None

    pnls = [float(t["pnl"]) for t in closed]
    n = len(pnls)
    sims = max(50, min(int(simulations), 2000))
    rng = random.Random(seed)

    totals: list[float] = []
    for _ in range(sims):
        sample = [pnls[rng.randrange(n)] for _ in range(n)]
        totals.append(sum(sample))

    totals.sort()
    p5 = totals[int(0.05 * (sims - 1))]
    p50 = totals[int(0.50 * (sims - 1))]
    p95 = totals[int(0.95 * (sims - 1))]
    base = float(starting_equity) if starting_equity > 0 else 10_000.0

    def _ret(pnl: float) -> float:
        return round(pnl / base * 100, 2)

    fan: list[dict] = []
    for step in range(1, n + 1):
        step_totals: list[float] = []
        for _ in range(min(sims, 300)):
            sample = [pnls[rng.randrange(n)] for _ in range(step)]
            step_totals.append(sum(sample))
        step_totals.sort()
        s5 = _percentile(step_totals, 0.05)
        s50 = _percentile(step_totals, 0.50)
        s95 = _percentile(step_totals, 0.95)
        fan.append({
            "step": step,
            "pnl_p5": round(s5, 2),
            "pnl_p50": round(s50, 2),
            "pnl_p95": round(s95, 2),
            "equity_p5": round(base + s5, 2),
            "equity_p50": round(base + s50, 2),
            "equity_p95": round(base + s95, 2),
        })

    return {
        "simulations": sims,
        "trade_count": n,
        "pnl_p5": round(p5, 2),
        "pnl_p50": round(p50, 2),
        "pnl_p95": round(p95, 2),
        "return_p5_pct": _ret(p5),
        "return_p50_pct": _ret(p50),
        "return_p95_pct": _ret(p95),
        "fan_bands": _downsample_fan(fan),
    }
