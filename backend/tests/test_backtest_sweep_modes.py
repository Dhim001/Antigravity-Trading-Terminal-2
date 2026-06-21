"""Tests for sweep modes — grid, random, Latin hypercube."""

from __future__ import annotations

import unittest

from app.services.bots.backtest_sweep import (
    MAX_SWEEP_COMBOS,
    MAX_SWEEP_COMBOS_EXTENDED,
    estimate_sweep_combos,
    expand_sweep_grid,
)


class TestSweepModes(unittest.TestCase):
    def test_grid_truncates_at_24(self):
        sweep = {
            "sweep_mode": "grid",
            "max_combos": 24,
            "trailing_stop_percent": [1, 2, 3, 4, 5],
            "take_profit_percent": [1, 2, 3, 4, 5],
        }
        combos = expand_sweep_grid({}, sweep)
        self.assertEqual(len(combos), MAX_SWEEP_COMBOS)

    def test_random_samples_up_to_100(self):
        sweep = {
            "sweep_mode": "random",
            "max_combos": 50,
            "sweep_seed": 42,
            "trailing_stop_percent": [1, 2, 3, 4, 5, 6, 7, 8],
            "take_profit_percent": [1, 2, 3, 4, 5, 6, 7, 8],
        }
        combos = expand_sweep_grid({"allocation": 1000}, sweep)
        self.assertEqual(len(combos), 50)
        self.assertTrue(all(c.get("allocation") == 1000 for c in combos))

    def test_lhs_respects_max_combos(self):
        sweep = {
            "sweep_mode": "lhs",
            "max_combos": 30,
            "sweep_seed": 7,
            "trailing_stop_percent": [1, 2, 3, 4],
            "take_profit_percent": [1, 2, 3, 4],
            "min_confidence": [0.5, 0.6, 0.7],
        }
        combos = expand_sweep_grid({}, sweep)
        self.assertLessEqual(len(combos), 30)
        self.assertGreater(len(combos), 0)

    def test_estimate_sweep_combos_truncated(self):
        est = estimate_sweep_combos({
            "sweep_mode": "grid",
            "max_combos": 24,
            "trailing_stop_percent": [1, 2, 3, 4, 5],
            "take_profit_percent": [1, 2, 3, 4, 5],
        })
        self.assertEqual(est["full_grid"], 25)
        self.assertTrue(est["truncated"])
        self.assertEqual(est["estimated"], 24)

    def test_random_cap_is_100(self):
        est = estimate_sweep_combos({
            "sweep_mode": "random",
            "max_combos": 200,
            "trailing_stop_percent": [1, 2],
        })
        self.assertEqual(est["max_combos"], MAX_SWEEP_COMBOS_EXTENDED)


if __name__ == "__main__":
    unittest.main()
