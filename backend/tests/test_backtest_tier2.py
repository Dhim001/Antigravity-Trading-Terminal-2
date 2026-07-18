"""Tier 2 search engine — Bayesian, stability, multi-objective."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.bots.backtest_bayesian import is_bayesian_sweep, run_bayesian_sweep
from app.services.bots.backtest_multi_objective import pareto_frontier, robust_score, stress_pnl_value
from app.services.bots.backtest_param_stability import analyze_parameter_stability, compute_centroid_config
from app.services.bots.backtest_sweep import expand_sweep_grid, is_sweep_request
from app.services.bots.backtest_walk_forward import row_objective_value


class TestBayesianSweep(unittest.TestCase):
    def test_is_bayesian_sweep(self):
        self.assertTrue(is_bayesian_sweep({"sweep_mode": "bayesian"}))
        self.assertFalse(is_bayesian_sweep({"sweep_mode": "grid"}))

    def test_expand_returns_empty_for_bayesian(self):
        combos = expand_sweep_grid(
            {"allocation": 1000},
            {"sweep_mode": "bayesian", "trailing_stop_percent": [1, 2, 3]},
        )
        self.assertEqual(combos, [])

    def test_is_sweep_request_bayesian(self):
        self.assertTrue(
            is_sweep_request(
                {"sweep_mode": "bayesian", "trailing_stop_percent": [1, 2]},
                [],
            )
        )

    def test_run_bayesian_sweep_sequential(self):
        calls = {"n": 0}

        def evaluate(cfg):
            calls["n"] += 1
            pnl = float(cfg.get("trailing_stop_percent", 1)) * 5
            return {
                "summary": {"total_pnl": pnl, "sharpe_ratio": pnl / 10, "total_trades": 10},
                "total_pnl": pnl,
                "trade_count": 10,
            }

        try:
            rows, meta = run_bayesian_sweep(
                base_config={"allocation": 1000},
                sweep={
                    "sweep_mode": "bayesian",
                    "max_combos": 6,
                    "bayesian_patience": 3,
                    "bayesian_startup_trials": 2,
                    "sweep_seed": 1,
                    "trailing_stop_percent": [1, 2, 3, 4],
                },
                evaluate_fn=evaluate,
                objective="total_pnl",
                min_trades=1,
            )
        except RuntimeError:
            self.skipTest("optuna not installed")
        self.assertGreater(len(rows), 0)
        self.assertEqual(meta["sweep_mode"], "bayesian")
        self.assertGreater(calls["n"], 0)


class TestParamStability(unittest.TestCase):
    def test_centroid_numeric(self):
        rows = [
            {"config": {"trailing_stop_percent": 1.0, "min_confidence": 0.5}},
            {"config": {"trailing_stop_percent": 3.0, "min_confidence": 0.7}},
        ]
        centroid = compute_centroid_config(rows, axes=[("trailing_stop_percent", []), ("min_confidence", [])])
        self.assertEqual(centroid["trailing_stop_percent"], 2)
        self.assertAlmostEqual(centroid["min_confidence"], 0.6)

    def test_analyze_recommends_centroid_on_spread(self):
        rows = [
            {"config": {"a": 1}, "total_pnl": 100, "trade_count": 10, "summary": {"total_pnl": 100}},
            {"config": {"a": 2}, "total_pnl": 80, "trade_count": 10, "summary": {"total_pnl": 80}},
            {"config": {"a": 3}, "total_pnl": 60, "trade_count": 10, "summary": {"total_pnl": 60}},
            {"config": {"a": 4}, "total_pnl": 40, "trade_count": 10, "summary": {"total_pnl": 40}},
        ]
        out = analyze_parameter_stability(
            rows,
            objective="total_pnl",
            min_trades=1,
            top_fraction=0.5,
            axes=[("a", [1, 2, 3, 4])],
        )
        self.assertEqual(out["recommendation"], "centroid")
        self.assertIsNotNone(out["stable_pick"])


class TestMultiObjective(unittest.TestCase):
    def test_robust_score(self):
        row = {
            "summary": {"sharpe_ratio": 1.5},
            "trade_count": 25,
        }
        self.assertGreater(robust_score(row), 0)

    def test_stress_pnl(self):
        row = {
            "total_pnl": 100,
            "trade_count": 10,
            "summary": {"total_fees": 5, "slippage_bps": 10},
            "config": {"allocation": 1000, "slippage_bps": 10},
        }
        self.assertLess(stress_pnl_value(row), 100)

    def test_pareto_frontier(self):
        rows = [
            {"total_pnl": 100, "trade_count": 5, "summary": {"max_drawdown": 10}},
            {"total_pnl": 80, "trade_count": 8, "summary": {"max_drawdown": 5}},
            {"total_pnl": 50, "trade_count": 3, "summary": {"max_drawdown": 20}},
        ]
        front = pareto_frontier(rows)
        self.assertGreaterEqual(len(front), 1)

    def test_new_objectives(self):
        row = {
            "total_pnl": 40,
            "trade_count": 8,
            "summary": {
                "expectancy": 5,
                "win_rate": 55,
                "max_consecutive_losses": 2,
                "sharpe_ratio": 1.2,
                "total_fees": 1,
                "slippage_bps": 5,
            },
            "config": {"allocation": 1000},
        }
        self.assertEqual(row_objective_value(row, "expectancy"), 5)
        self.assertEqual(row_objective_value(row, "win_rate"), 55)
        self.assertEqual(row_objective_value(row, "max_consecutive_losses"), -2)

    def test_ml_objectives(self):
        from app.services.bots.backtest_walk_forward import (
            VALID_SWEEP_OBJECTIVES,
            slim_ml_metrics_for_sweep,
        )

        for key in ("auc_roc", "log_loss", "alpha_decay_half_life", "oos_is_ratio"):
            self.assertIn(key, VALID_SWEEP_OBJECTIVES)

        row = {
            "total_pnl": 10,
            "trade_count": 5,
            "summary": {"sharpe_ratio": 1.0},
            "ml_metrics": {
                "auc_roc": 0.72,
                "log_loss": 0.4,
                "alpha_decay": {"half_life_days": 12.5},
                "is_vs_oos": {"is_sharpe": 2.0, "oos_sharpe": 1.0},
            },
        }
        self.assertAlmostEqual(row_objective_value(row, "auc_roc"), 0.72)
        self.assertAlmostEqual(row_objective_value(row, "log_loss"), -0.4)
        self.assertAlmostEqual(row_objective_value(row, "alpha_decay_half_life"), 12.5)
        self.assertAlmostEqual(row_objective_value(row, "oos_is_ratio"), 0.5)

        slim = slim_ml_metrics_for_sweep(row["ml_metrics"])
        self.assertIn("auc_roc", slim)
        self.assertIn("alpha_decay", slim)
        self.assertNotIn("confusion_matrix", slim or {})


if __name__ == "__main__":
    unittest.main()
