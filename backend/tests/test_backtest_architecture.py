"""Tests for shared strategy runtime and backtest job tiers."""

import unittest

from app.services.bots.backtest_perf import (
    backtest_tier_meta,
    classify_backtest_tier,
    estimate_backtest_seconds,
)
from app.services.bots.backtest_walk_forward import aggregate_regime_oos
from app.services.bots.strategy_runtime import (
    ExecutionChain,
    apply_indicator_parity_gates,
    chart_filter_reject_block,
)


class _StubFilter:
    def __init__(self, allowed: bool = True, reason: str = "blocked"):
        self._allowed = allowed
        self._reason = reason

    def evaluate_gate(self, _row, _signal):
        return self._allowed, None if self._allowed else self._reason


class TestStrategyRuntime(unittest.TestCase):
    def test_htf_blocks_buy_in_bear(self):
        lookup = [(100, "BEAR")]
        out = apply_indicator_parity_gates(
            "BUY",
            row={},
            bar_time=200,
            live_parity=True,
            strat_key="EMA_CROSS",
            confirm_tf="4h",
            htf_bias_lookup=lookup,
            strat_filter=None,
        )
        self.assertIsNone(out.signal)
        self.assertEqual(out.block.kind, "parity_htf")

    def test_chart_filter_reject_bucket(self):
        block = chart_filter_reject_block(
            {"reject_reason": "trend score 0 does not align with BUY", "signal": "NONE"},
        )
        self.assertEqual(block.bucket, "trend")

    def test_execution_chain_stages(self):
        chain = ExecutionChain(12345)
        chain.record("signal", ok=True, signal="BUY")
        chain.record("fill", ok=True, price=100.5)
        stages = [e["stage"] for e in chain.to_list()]
        self.assertEqual(stages, ["signal", "fill"])


class TestBacktestTiers(unittest.TestCase):
    def test_portfolio_is_deferred(self):
        tier = classify_backtest_tier({
            "days": 7,
            "portfolio_symbols": ["AAPL", "MSFT"],
        })
        self.assertEqual(tier, "deferred")

    def test_short_baseline_inline(self):
        tier = classify_backtest_tier({"days": 7, "strategy": "EMA_CROSS"})
        self.assertEqual(tier, "inline")

    def test_tier_meta_includes_estimate(self):
        meta = backtest_tier_meta({"days": 7, "sweep": {"trailing_stop_percent": [1, 2, 3]}})
        self.assertIn(meta["tier"], ("inline", "deferred"))
        self.assertGreater(meta["estimated_sec"], 0)


class TestRegimeWalkForward(unittest.TestCase):
    def test_regime_analysis_flags_single_regime_risk(self):
        folds = [
            {
                "oos_regime": {"dominant_regime": "elevated"},
                "out_of_sample": {"total_pnl": 50},
            },
            {
                "oos_regime": {"dominant_regime": "elevated"},
                "out_of_sample": {"total_pnl": 30},
            },
        ]
        analysis = aggregate_regime_oos(folds)
        self.assertTrue(analysis["single_regime_risk"])
        self.assertIn("elevated", analysis["per_regime"])


if __name__ == "__main__":
    unittest.main()
