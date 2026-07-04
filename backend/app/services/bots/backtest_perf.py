"""Backtest performance helpers — parallelism caps, tier routing, and estimates."""

from __future__ import annotations

from typing import Any

from app.config import (
    BACKTEST_DEFER_HEAVY,
    BACKTEST_INLINE_MAX_SEC,
    BACKTEST_PARALLEL_WORKERS,
)


def parallel_worker_count(task_count: int) -> int:
    """Bounded worker count for embarrassingly parallel backtest tasks."""
    tasks = max(0, int(task_count or 0))
    if tasks <= 1:
        return 1
    cap = max(1, min(int(BACKTEST_PARALLEL_WORKERS), 8))
    return min(cap, tasks)


def _sweep_combo_count(sweep: dict | list | None) -> int:
    if not sweep:
        return 1
    if isinstance(sweep, dict):
        mode = str(sweep.get("sweep_mode") or "grid").lower()
        max_combos = int(sweep.get("max_combos") or 24)
        if mode in ("random", "lhs"):
            return max(1, min(max_combos, 100))
        total = 1
        for vals in sweep.values():
            if isinstance(vals, list) and vals:
                total *= len(vals)
        return max(1, min(total, 24))
    if isinstance(sweep, list):
        return max(1, len(sweep))
    return 1


def estimate_backtest_seconds(
    *,
    days: int = 7,
    sweep: dict | list | None = None,
    walk_forward: bool = False,
    reasoning: bool = False,
    portfolio_symbols: list | None = None,
    meta_label_walk_forward: bool = False,
    rolling_folds: int = 1,
) -> float:
    """Rough server-side duration estimate for tier routing."""
    parsed_days = max(1, int(days or 7))
    symbol_count = len(portfolio_symbols) if portfolio_symbols else 1
    combos = _sweep_combo_count(sweep)
    folds = max(1, int(rolling_folds or 1))

    sec = 4.0 + parsed_days * 0.9
    if symbol_count > 1:
        sec *= symbol_count * 0.75
    if combos > 1:
        sec *= min(combos, 24) * 0.45
    if walk_forward and sweep:
        sec *= folds * 1.6
    elif walk_forward:
        sec *= 1.4
    if reasoning:
        sec *= 2.5
    if meta_label_walk_forward:
        sec *= folds * 2.2
    if parsed_days >= 30:
        sec *= 1.35
    return round(sec, 1)


def is_heavy_backtest(
    *,
    days: int = 7,
    sweep: dict | list | None = None,
    walk_forward: bool = False,
    reasoning: bool = False,
    portfolio_symbols: list | None = None,
    meta_label_walk_forward: bool = False,
) -> bool:
    """True when the run should execute in a background task (not block the WS handler)."""
    if not BACKTEST_DEFER_HEAVY:
        return False
    if portfolio_symbols and len(portfolio_symbols) > 1:
        return True
    if reasoning:
        return True
    if walk_forward and sweep:
        return True
    if sweep:
        return True
    if meta_label_walk_forward:
        return True
    if int(days or 7) >= 30:
        return True
    return False


def classify_backtest_tier(req: dict[str, Any] | None) -> str:
    """Fast (<30s) inline; slow portfolio/WF/reasoning deferred to job queue."""
    req = req or {}
    config = req.get("config") or {}
    days = int(req.get("days") or 7)
    sweep = req.get("sweep")
    walk_forward = bool(req.get("walk_forward"))
    reasoning = bool(req.get("reasoning"))
    portfolio_symbols = req.get("portfolio_symbols")
    meta_label_wf = bool(config.get("meta_label_walk_forward"))
    rolling_folds = int(req.get("rolling_folds") or 1)

    if is_heavy_backtest(
        days=days,
        sweep=sweep,
        walk_forward=walk_forward,
        reasoning=reasoning,
        portfolio_symbols=portfolio_symbols,
        meta_label_walk_forward=meta_label_wf,
    ):
        return "deferred"

    est = estimate_backtest_seconds(
        days=days,
        sweep=sweep,
        walk_forward=walk_forward,
        reasoning=reasoning,
        portfolio_symbols=portfolio_symbols,
        meta_label_walk_forward=meta_label_wf,
        rolling_folds=rolling_folds,
    )
    if est > float(BACKTEST_INLINE_MAX_SEC):
        return "deferred"
    return "inline"


def heavy_backtest_label(req: dict[str, Any]) -> str:
    if req.get("portfolio_symbols") and len(req["portfolio_symbols"]) > 1:
        return "portfolio"
    if req.get("reasoning"):
        return "reasoning"
    if req.get("walk_forward") and req.get("sweep"):
        return "walk-forward"
    if req.get("sweep"):
        return "sweep"
    cfg = req.get("config") or {}
    if cfg.get("meta_label_walk_forward"):
        return "meta-label-wf"
    return "long-range"


def backtest_tier_meta(req: dict[str, Any] | None) -> dict[str, Any]:
    """Metadata attached to jobs and run manifests."""
    req = req or {}
    config = req.get("config") or {}
    tier = classify_backtest_tier(req)
    est = estimate_backtest_seconds(
        days=int(req.get("days") or 7),
        sweep=req.get("sweep"),
        walk_forward=bool(req.get("walk_forward")),
        reasoning=bool(req.get("reasoning")),
        portfolio_symbols=req.get("portfolio_symbols"),
        meta_label_walk_forward=bool(config.get("meta_label_walk_forward")),
        rolling_folds=int(req.get("rolling_folds") or 1),
    )
    return {
        "tier": tier,
        "estimated_sec": est,
        "label": heavy_backtest_label({**req, "config": config}) if tier == "deferred" else "baseline",
    }
