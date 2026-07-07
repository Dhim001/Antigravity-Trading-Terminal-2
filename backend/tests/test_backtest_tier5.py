"""Tier 5 engine performance — trial budget, indicator cache, job isolation."""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from app.services.bots.backtest_indicator_cache import (
    indicator_fingerprint,
    unique_indicator_configs,
)
from app.services.bots.backtest_perf import classify_backtest_tier, is_heavy_backtest
from app.services.bots.backtest_trial_budget import (
    TrialBudgetTracker,
    resolve_max_trials,
    resolve_trial_budget,
)


class TestIndicatorFingerprint(unittest.TestCase):
    def test_risk_only_params_share_fingerprint(self):
        base = {"rsi_length": 14, "trailing_stop_percent": 1.0}
        alt = {"rsi_length": 14, "trailing_stop_percent": 3.0}
        self.assertEqual(
            indicator_fingerprint("MACD_RSI", base),
            indicator_fingerprint("MACD_RSI", alt),
        )

    def test_indicator_param_changes_fingerprint(self):
        a = {"rsi_length": 14}
        b = {"rsi_length": 21}
        self.assertNotEqual(
            indicator_fingerprint("MACD_RSI", a),
            indicator_fingerprint("MACD_RSI", b),
        )

    def test_unique_indicator_configs_dedupes_risk_sweeps(self):
        configs = [
            {"rsi_length": 14, "trailing_stop_percent": 1},
            {"rsi_length": 14, "trailing_stop_percent": 2},
            {"rsi_length": 21, "trailing_stop_percent": 1},
        ]
        unique = unique_indicator_configs("MACD_RSI", configs)
        self.assertEqual(len(unique), 2)


class TestTrialBudget(unittest.TestCase):
    def test_resolve_max_trials_caps_grid(self):
        self.assertEqual(
            resolve_max_trials({"max_combos": 50, "sweep_mode": "grid"}, "grid"),
            24,
        )

    def test_resolve_max_trials_extended_mode(self):
        sweep = {"max_combos": 150, "max_trials": 80, "sweep_mode": "random"}
        self.assertEqual(resolve_max_trials(sweep, "random"), 80)

    def test_trial_budget_stops_at_max_trials(self):
        tracker = TrialBudgetTracker({"max_trials": 2, "time_budget_sec": 0})
        tracker.record_trial()
        tracker.record_trial()
        self.assertTrue(tracker.should_stop())
        self.assertEqual(tracker.stopped_reason, "max_trials")

    def test_trial_budget_stops_on_time(self):
        tracker = TrialBudgetTracker({"max_trials": 100, "time_budget_sec": 0.01})
        time.sleep(0.02)
        self.assertTrue(tracker.should_stop())
        self.assertEqual(tracker.stopped_reason, "time_budget")

    def test_resolve_trial_budget_meta(self):
        meta = resolve_trial_budget({"sweep_mode": "bayesian", "max_trials": 50})
        self.assertEqual(meta["max_trials"], 50)
        self.assertGreater(meta["time_budget_sec"], 0)


class TestJobIsolation(unittest.TestCase):
    @patch("app.config.BACKTEST_DEFER_HEAVY", False)
    @patch("app.config.BACKTEST_FORCE_DEFER_OPTIMIZATION", True)
    def test_walk_forward_always_deferred(self):
        self.assertTrue(is_heavy_backtest(walk_forward=True, days=7))
        self.assertEqual(
            classify_backtest_tier({"days": 7, "walk_forward": True}),
            "deferred",
        )

    @patch("app.config.BACKTEST_DEFER_HEAVY", False)
    @patch("app.config.BACKTEST_FORCE_DEFER_OPTIMIZATION", True)
    def test_sweep_always_deferred(self):
        self.assertTrue(is_heavy_backtest(sweep={"trailing_stop_percent": [1, 2]}))
        self.assertEqual(
            classify_backtest_tier({"days": 7, "sweep": {"trailing_stop_percent": [1, 2]}}),
            "deferred",
        )


if __name__ == "__main__":
    unittest.main()
