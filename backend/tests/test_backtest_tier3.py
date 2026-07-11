"""Tier 3 validation methodology — purged WF, anchored WF, holdout, PBO."""

from __future__ import annotations

import unittest

from app.services.bots.backtest_pbo import compute_pbo_from_matrix
from app.services.bots.backtest_purged_cv import (
    apply_embargo_after_test,
    embargo_bars_for_segment,
    parse_wf_validation_options,
    purge_train_before_test,
    split_final_holdout,
)
from app.services.bots.backtest_walk_forward import (
    build_anchored_fold_windows,
    build_fold_windows,
    build_rolling_fold_windows,
    split_train_test,
)
from app.services.bots.deploy_gate import evaluate_deploy_gate


def _candles(n: int, start: int = 1_000_000) -> list[dict]:
    return [{"time": start + i * 60_000, "close": 100.0 + i * 0.01} for i in range(n)]


class TestPurgedSplits(unittest.TestCase):
    def test_purge_removes_tail_bars(self):
        train = _candles(100)
        test = _candles(50, start=2_000_000)
        purged, meta = purge_train_before_test(train, test, purge_bars=10)
        self.assertTrue(meta.get("purged"))
        self.assertEqual(len(purged), 90)

    def test_split_train_test_applies_purge(self):
        candles = _candles(200)
        opts = {"purged_splits": True, "purge_bars": 8}
        train, test, train_meta, _ = split_train_test(candles, {}, 70.0, wf_options=opts)
        self.assertIn("purge", train_meta)
        self.assertLess(len(train), int(200 * 0.7))

    def test_embargo_advances_index(self):
        candles = _candles(100)
        emb = embargo_bars_for_segment(50, 2.0)
        self.assertEqual(emb, 1)
        idx = apply_embargo_after_test(candles, 50, emb)
        self.assertEqual(idx, 51)


class TestFinalHoldout(unittest.TestCase):
    def test_split_reserves_trailing_segment(self):
        candles = _candles(500)
        wf, holdout, wf_meta, ho_meta = split_final_holdout(candles, {}, 10.0)
        self.assertEqual(len(wf) + len(holdout), 500)
        self.assertGreaterEqual(len(holdout), 50)
        self.assertEqual(wf_meta.get("window"), "walk_forward")
        self.assertEqual(ho_meta.get("window"), "final_holdout")


class TestAnchoredWalkForward(unittest.TestCase):
    def test_anchored_produces_multiple_folds(self):
        candles = _candles(600)
        opts = {"wf_mode": "anchored", "wf_step_pct": 20.0, "purged_splits": False}
        windows = build_anchored_fold_windows(
            candles,
            {},
            train_pct=70.0,
            wf_step_pct=20.0,
            max_folds=4,
            wf_options=opts,
        )
        self.assertGreaterEqual(len(windows), 2)
        first_train = windows[0][0]
        second_train = windows[1][0]
        self.assertLess(len(first_train), len(second_train))

    def test_build_fold_windows_dispatches_anchored(self):
        candles = _candles(600)
        opts = {"wf_mode": "anchored", "wf_step_pct": 25.0, "purged_splits": False}
        windows = build_fold_windows(
            candles,
            {},
            rolling_folds=3,
            train_pct=70.0,
            wf_options=opts,
        )
        self.assertGreaterEqual(len(windows), 2)

    def test_rolling_with_embargo(self):
        candles = _candles(500)
        opts = {"purged_splits": True, "purge_bars": 5, "embargo_pct": 1.0}
        windows = build_rolling_fold_windows(
            candles,
            {},
            rolling_folds=3,
            train_pct=70.0,
            wf_options=opts,
        )
        self.assertGreaterEqual(len(windows), 2)


class TestPboAudit(unittest.TestCase):
    def test_compute_pbo_high_when_is_winner_fails_oos(self):
        # Strategy 0 peaks on early groups only — classic overfit signature
        matrix = [
            [100.0, 10.0],
            [100.0, 10.0],
            [100.0, 10.0],
            [-50.0, 10.0],
            [-50.0, 10.0],
            [-50.0, 10.0],
        ]
        out = compute_pbo_from_matrix(matrix, n_test_groups=3)
        self.assertIsNotNone(out.get("pbo"))
        self.assertGreaterEqual(out["pbo"], 0.35)

    def test_compute_pbo_low_when_robust(self):
        matrix = [
            [10.0, 5.0],
            [9.0, 6.0],
            [11.0, 4.0],
            [10.0, 5.0],
            [8.0, 7.0],
            [10.0, 6.0],
        ]
        out = compute_pbo_from_matrix(matrix, n_test_groups=3)
        self.assertIsNotNone(out.get("pbo"))
        self.assertLess(out["pbo"], 0.5)


class TestWfValidationOptions(unittest.TestCase):
    def test_defaults_purged_true(self):
        opts = parse_wf_validation_options({}, msg={}, base_config={})
        self.assertTrue(opts["purged_splits"])
        self.assertGreater(opts["purge_bars"], 0)
        self.assertEqual(opts["wf_mode"], "rolling")

    def test_holdout_and_pbo_from_sweep(self):
        opts = parse_wf_validation_options(
            {"final_holdout_pct": 12, "pbo_audit": True, "wf_mode": "anchored"},
            msg={},
        )
        self.assertEqual(opts["final_holdout_pct"], 12.0)
        self.assertTrue(opts["pbo_audit"])
        self.assertEqual(opts["wf_mode"], "anchored")


class TestDeployGateHoldout(unittest.TestCase):
    def test_blocks_failed_holdout(self):
        results = {
            "walk_forward": {
                "out_of_sample": {"total_pnl": 100, "trade_count": 5},
                "aggregate": {"fold_count": 1},
            },
            "final_holdout": {
                "total_pnl": -10,
                "trade_count": 2,
                "passed": False,
            },
        }
        gate = evaluate_deploy_gate(results, min_trades=1, min_pnl=0)
        holdout_checks = [c for c in gate["checks"] if c["id"] == "final_holdout"]
        self.assertEqual(len(holdout_checks), 1)
        self.assertFalse(holdout_checks[0]["ok"])
        self.assertTrue(gate["blocking"])


class TestWfMinTradesFloor(unittest.TestCase):
    def test_walk_forward_uses_relaxed_per_param_floor(self):
        from app.services.bots.backtest_walk_forward import _resolve_min_trades

        effective, meta = _resolve_min_trades(
            1,
            [{"trailing_stop_percent": 1}, {"trailing_stop_percent": 2}],
            {"trailing_stop_percent": [1, 2]},
            walk_forward=True,
        )
        self.assertEqual(effective, 5)
        self.assertEqual(meta["trades_per_param_rule"], 5)
        self.assertTrue(meta["walk_forward_floor"])

    def test_single_value_sweep_axes_do_not_inflate_floor(self):
        from app.services.bots.backtest_walk_forward import _resolve_min_trades

        effective, meta = _resolve_min_trades(
            1,
            [{"trailing_stop_percent": 1, "min_confidence": 0.55}],
            {
                "trailing_stop_percent": [1],
                "min_confidence": [0.55],
                "require_trend_alignment": [False],
            },
            walk_forward=True,
        )
        self.assertEqual(meta["swept_param_axes"], 0)
        self.assertEqual(effective, 1)


if __name__ == "__main__":
    unittest.main()
