"""Rule engine parity vs frontend generateSignal scorer."""

from __future__ import annotations

import math
import unittest

from app.services.agent.feature_builder import FeatureBuilder
from app.services.agent.rule_engine import display_label, score_dataframe
from app.services.bots.screener import MarketScreenerService


def make_trending_candles(count: int = 120, start: float = 100.0, drift: float = 0.001) -> list:
    candles = []
    base_time = 1_700_000_000
    price = start
    for i in range(count):
        o = price
        h = price * 1.002
        l = price * 0.998
        c = price * (1 + drift)
        candles.append({
            "time": base_time + i * 60,
            "open": round(o, 4),
            "high": round(h, 4),
            "low": round(l, 4),
            "close": round(c, 4),
            "volume": 10_000,
        })
        price = c
    return candles


class TestChartAgentRules(unittest.TestCase):
    def setUp(self):
        self.builder = FeatureBuilder(MarketScreenerService())

    def test_scores_closed_bar_with_reasons(self):
        candles = make_trending_candles(200, drift=0.0008)
        df = self.builder.build("BTCUSDT", candles)
        self.assertFalse(df.empty)
        insight = score_dataframe(df, "BTCUSDT")
        self.assertIsNotNone(insight)
        self.assertIsInstance(insight.score, int)
        self.assertGreaterEqual(insight.confidence, 0.0)
        self.assertLessEqual(insight.confidence, 1.0)
        self.assertTrue(insight.reasons)
        self.assertEqual(insight.insight_id, f"BTCUSDT:1m:{insight.bar_time}")

    def test_display_label_matches_score_thresholds(self):
        self.assertEqual(display_label(4), "STRONG BUY")
        self.assertEqual(display_label(2), "BUY")
        self.assertEqual(display_label(0), "NEUTRAL")
        self.assertEqual(display_label(-2), "SELL")
        self.assertEqual(display_label(-5), "STRONG SELL")

    def test_confidence_normalized_by_score_magnitude(self):
        candles = make_trending_candles(200)
        df = self.builder.build("ETHUSDT", candles)
        insight = score_dataframe(df, "ETHUSDT")
        expected = round(1.0 / (1.0 + math.exp(-0.8 * (abs(insight.score) - 3))), 3)
        self.assertEqual(insight.confidence, expected)

    def test_sub_reports_sum_to_composite_score(self):
        candles = make_trending_candles(200, drift=0.0008)
        df = self.builder.build("BTCUSDT", candles)
        insight = score_dataframe(df, "BTCUSDT")
        self.assertIsNotNone(insight.sub_reports)
        trend = insight.sub_reports["trend"]["score"]
        momentum = insight.sub_reports["momentum"]["score"]
        self.assertEqual(insight.score, trend + momentum)
        self.assertIn("risk", insight.sub_reports)
        self.assertIn("indicator", insight.sub_reports)
        self.assertEqual(insight.sub_reports["indicator"]["score"], momentum)
        self.assertEqual(insight.version, 2)

    def test_v1_payload_deserializes_without_sub_reports(self):
        from app.services.agent.models import ChartAgentInsight
        payload = {
            "symbol": "AAPL",
            "bar_time": 1700000000,
            "signal": "BUY",
            "score": 3,
            "confidence": 0.75,
            "reasons": ["test"],
        }
        insight = ChartAgentInsight.from_dict(payload)
        self.assertEqual(insight.version, 1)
        self.assertIsNone(insight.sub_reports)

    def test_bot_signal_only_buy_sell_none(self):
        candles = make_trending_candles(200)
        df = self.builder.build("AAPL", candles)
        insight = score_dataframe(df, "AAPL")
        self.assertIn(insight.signal, ("BUY", "SELL", "NONE"))
        if abs(insight.score) < 2:
            self.assertEqual(insight.signal, "NONE")


if __name__ == "__main__":
    unittest.main()
