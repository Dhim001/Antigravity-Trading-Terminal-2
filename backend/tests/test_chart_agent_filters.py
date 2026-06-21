"""CHART_AGENT entry filters, vol sizing metadata, and cache-miss reasons."""

from __future__ import annotations

import unittest

from app.database import init_db
from app.services.agent.chart_analyst import init_chart_analyst
from app.services.agent.models import insight_cache_key
from app.services.bots.screener import MarketScreenerService
from app.services.bots.strategies_chart_agent import (
    ChartAgentStrategy,
    build_signal_from_insight,
    check_entry_filters,
    classify_filter_reject,
    compact_insight_snapshot,
)
from tests.test_chart_agent_rules import make_trending_candles


def _insight(
    *,
    signal="BUY",
    confidence=0.8,
    score=2,
    trend_score=1,
    momentum_score=1,
    atr_regime="normal",
    size_factor=1.0,
    bar_time=100,
):
    return {
        "symbol": "BTCUSDT",
        "bar_time": bar_time,
        "timeframe": "1m",
        "signal": signal,
        "confidence": confidence,
        "score": score,
        "reasons": ["trend up", "macd bullish"],
        "levels": {"stop_loss_distance": 50.0},
        "insight_id": f"BTCUSDT:1m:{bar_time}",
        "sub_reports": {
            "trend": {"score": trend_score, "reasons": ["trend up"]},
            "momentum": {"score": momentum_score, "reasons": ["macd bullish"]},
            "risk": {
                "score": 0,
                "atr_regime": atr_regime,
                "suggested_size_factor": size_factor,
                "reasons": [],
            },
        },
    }


class TestChartAgentFilters(unittest.TestCase):
    def setUp(self):
        init_db()
        self.cfg = {"symbol": "BTCUSDT", "timeframe": "1m", "min_confidence": 0.55}

    def test_build_signal_includes_size_factor(self):
        out = build_signal_from_insight(_insight(size_factor=0.8), self.cfg)
        self.assertEqual(out["signal"], "BUY")
        self.assertAlmostEqual(out["size_factor"], 0.8)
        self.assertIn("insight_snapshot", out)

    def test_require_trend_alignment_blocks_weak_buy(self):
        cfg = {**self.cfg, "require_trend_alignment": True}
        reject = check_entry_filters(_insight(trend_score=0), cfg, "BUY")
        self.assertIsNotNone(reject)
        out = build_signal_from_insight(_insight(trend_score=0), cfg)
        self.assertEqual(out["signal"], "NONE")
        self.assertIn("trend", out["reject_reason"])

    def test_block_elevated_vol(self):
        cfg = {**self.cfg, "block_elevated_vol": True}
        out = build_signal_from_insight(_insight(atr_regime="elevated"), cfg)
        self.assertEqual(out["signal"], "NONE")
        self.assertIn("elevated", out["reject_reason"])

    def test_min_score_filter(self):
        cfg = {**self.cfg, "min_score": 3}
        out = build_signal_from_insight(_insight(score=2), cfg)
        self.assertEqual(out["signal"], "NONE")
        self.assertIn("min_score", out["reject_reason"])

    def test_confirm_timeframe_requires_htf_trend(self):
        cfg = {**self.cfg, "confirm_timeframe": "4h"}
        confirm = _insight(trend_score=0, bar_time=99)
        out = build_signal_from_insight(_insight(), cfg, confirm_insight=confirm)
        self.assertEqual(out["signal"], "NONE")
        self.assertIn("4h", out["reject_reason"])

    def test_classify_filter_reject_buckets(self):
        self.assertEqual(classify_filter_reject("score 2 below min_score 3"), "min_score")
        self.assertEqual(classify_filter_reject("trend score 0 does not align with BUY"), "trend")
        self.assertEqual(classify_filter_reject("elevated ATR regime blocks entry"), "vol")
        self.assertEqual(classify_filter_reject("4h trend score 0 does not confirm BUY"), "htf")

    def test_compact_snapshot_shape(self):
        snap = compact_insight_snapshot(_insight())
        self.assertEqual(snap["signal"], "BUY")
        self.assertIn("sub_reports", snap)


class TestChartAgentStrategyIntegration(unittest.TestCase):
    def setUp(self):
        init_db()
        self.analyst = init_chart_analyst(screener=MarketScreenerService(), feed=None, broadcast_fn=None)
        self.candles = make_trending_candles(220)

    def test_evaluate_returns_reject_reason_on_cache_miss(self):
        strategy = ChartAgentStrategy({"symbol": "BTCUSDT", "timeframe": "1m"})
        result = strategy.evaluate({"time": 999999, "close": 100})
        self.assertEqual(result["signal"], "NONE")
        self.assertIn("reject_reason", result)

    def test_evaluate_with_cached_insight_and_filters(self):
        import asyncio

        insight = asyncio.run(
            self.analyst.analyze("BTCUSDT", candles=self.candles, broadcast=False)
        )
        self.assertIsNotNone(insight)

        strategy = ChartAgentStrategy({
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "min_confidence": 0.55,
            "block_elevated_vol": True,
        })
        result = strategy.evaluate({"time": insight.bar_time, "close": 100})
        if insight.confidence >= 0.55 and insight.signal in ("BUY", "SELL"):
            regime = (insight.sub_reports or {}).get("risk", {}).get("atr_regime")
            if regime != "elevated":
                self.assertEqual(result["signal"], insight.signal)
                self.assertIn("size_factor", result)


if __name__ == "__main__":
    unittest.main()
