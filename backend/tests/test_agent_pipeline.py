"""Tests for agent automation pipeline."""

import unittest

from app.services.agent.pipeline import (
    rank_scan_rows,
    validate_walk_forward_oos,
)


class TestRankScanRows(unittest.TestCase):
    def test_filters_low_confidence(self):
        rows = [
            {"symbol": "BTCUSDT", "signal": "BUY", "score": 4, "confidence": 0.8},
            {"symbol": "ETHUSDT", "signal": "BUY", "score": 4, "confidence": 0.4},
        ]
        ranked = rank_scan_rows(rows, min_confidence=0.55, min_score=2)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["symbol"], "BTCUSDT")

    def test_actionable_only(self):
        rows = [
            {"symbol": "BTCUSDT", "signal": "NONE", "score": 0, "confidence": 0.9},
            {"symbol": "SOLUSDT", "signal": "SELL", "score": -3, "confidence": 0.7},
        ]
        ranked = rank_scan_rows(rows, signal_filter="ACTIONABLE", min_confidence=0.5, min_score=2)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["symbol"], "SOLUSDT")


class TestWalkForwardOosGate(unittest.TestCase):
    def test_passes_when_oos_positive(self):
        ok, reason, metrics = validate_walk_forward_oos(
            {
                "walk_forward": {
                    "out_of_sample": {"total_pnl": 12.5, "total_trades": 3},
                },
            },
            min_oos_pnl=0,
            min_oos_trades=1,
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "OK")
        self.assertEqual(metrics["oos_trades"], 3)

    def test_fails_on_low_trades(self):
        ok, reason, _ = validate_walk_forward_oos(
            {"walk_forward": {"out_of_sample": {"total_pnl": 10, "total_trades": 0}}},
            min_oos_trades=2,
        )
        self.assertFalse(ok)
        self.assertIn("OOS trades", reason)


if __name__ == "__main__":
    unittest.main()
