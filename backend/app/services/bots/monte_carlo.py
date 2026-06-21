"""Monte Carlo bootstrap confidence bands from closed trade PnLs."""

from __future__ import annotations

import random
from typing import Any


def monte_carlo_trade_bands(
    trades: list[dict],
    *,
    starting_equity: float = 10_000.0,
    simulations: int = 500,
    seed: int = 42,
) -> dict[str, Any] | None:
    """
    Resample closed-trade PnLs with replacement to estimate total PnL distribution.
    Returns 5th/50th/95th percentile total PnL and return %.
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

    return {
        "simulations": sims,
        "trade_count": n,
        "pnl_p5": round(p5, 2),
        "pnl_p50": round(p50, 2),
        "pnl_p95": round(p95, 2),
        "return_p5_pct": _ret(p5),
        "return_p50_pct": _ret(p50),
        "return_p95_pct": _ret(p95),
    }
