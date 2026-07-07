"""Tier 5 — adaptive trial budget (time + max trials) for optimization runs."""

from __future__ import annotations

import time
from typing import Any

from app.config import (
    BACKTEST_SWEEP_MAX_GRID,
    BACKTEST_SWEEP_MAX_TRIALS,
    BACKTEST_SWEEP_TIME_BUDGET_SEC,
)
from app.services.bots.backtest_sweep import MAX_SWEEP_COMBOS, MAX_SWEEP_COMBOS_EXTENDED, SWEEP_MODES


def resolve_time_budget_sec(sweep: dict | None) -> float:
    sweep = sweep or {}
    raw = sweep.get("time_budget_sec")
    if raw is not None and str(raw).strip() != "":
        return max(0.0, float(raw))
    return float(BACKTEST_SWEEP_TIME_BUDGET_SEC)


def resolve_max_trials(sweep: dict | None, sweep_mode: str) -> int:
    """Effective combo/trial cap — replaces hard 24/100 when budget fields are set."""
    sweep = sweep or {}
    mode = str(sweep_mode or sweep.get("sweep_mode") or "grid").lower()
    if mode not in SWEEP_MODES:
        mode = "grid"

    legacy_cap = (
        MAX_SWEEP_COMBOS_EXTENDED
        if mode in ("random", "lhs", "bayesian")
        else MAX_SWEEP_COMBOS
    )
    env_cap = BACKTEST_SWEEP_MAX_TRIALS if mode != "grid" else BACKTEST_SWEEP_MAX_GRID

    requested = int(sweep.get("max_combos") or legacy_cap)
    max_trials = int(sweep.get("max_trials") or env_cap)
    return max(1, min(requested, max_trials, env_cap))


def resolve_trial_budget(sweep: dict | None) -> dict[str, Any]:
    """Budget metadata for UI + sweep expansion."""
    sweep = sweep or {}
    mode = str(sweep.get("sweep_mode") or "grid").lower()
    if mode not in SWEEP_MODES:
        mode = "grid"
    return {
        "sweep_mode": mode,
        "max_trials": resolve_max_trials(sweep, mode),
        "time_budget_sec": resolve_time_budget_sec(sweep),
        "legacy_grid_cap": MAX_SWEEP_COMBOS,
        "legacy_extended_cap": MAX_SWEEP_COMBOS_EXTENDED,
    }


class TrialBudgetTracker:
    """Stops sweeps when time or trial budget is exhausted."""

    def __init__(self, sweep: dict | None):
        self._sweep = sweep or {}
        self._mode = str(self._sweep.get("sweep_mode") or "grid").lower()
        self.max_trials = resolve_max_trials(self._sweep, self._mode)
        self.time_budget_sec = resolve_time_budget_sec(self._sweep)
        self._start = time.monotonic()
        self.trials_done = 0
        self.stopped_reason: str | None = None

    def record_trial(self) -> None:
        self.trials_done += 1

    @property
    def elapsed_sec(self) -> float:
        return time.monotonic() - self._start

    def should_stop(self) -> bool:
        if self.trials_done >= self.max_trials:
            self.stopped_reason = "max_trials"
            return True
        if self.time_budget_sec > 0 and self.elapsed_sec >= self.time_budget_sec:
            self.stopped_reason = "time_budget"
            return True
        return False

    def to_meta(self) -> dict[str, Any]:
        return {
            "trials_completed": self.trials_done,
            "trials_budget": self.max_trials,
            "time_budget_sec": self.time_budget_sec,
            "elapsed_sec": round(self.elapsed_sec, 1),
            "budget_exhausted": self.stopped_reason is not None,
            "stopped_reason": self.stopped_reason,
        }
