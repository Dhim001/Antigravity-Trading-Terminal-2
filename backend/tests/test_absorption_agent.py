"""Tests for multi-domain ABSORPTION_AGENT scoring."""

from __future__ import annotations

import unittest

from app.services.bots.indicators import atr_col
from app.services.bots.strategies_microstructure import AbsorptionAgentStrategy


def _base_row(**extra):
    row = {
        atr_col(14): 10.0,
        "volume_ma_20": 100.0,
        "volume": 200.0,
        "open": 100.0,
        "high": 110.0,
        "low": 99.0,
        "close": 109.0,  # large range, small body relative? body=9, range=11 — not absorption
        "EMA_9": 105.0,
        "EMA_21": 100.0,
        "ADX_14": 25.0,
        "rolling_low_20": 98.0,
        "rolling_high_20": 120.0,
    }
    row.update(extra)
    return row


class AbsorptionAgentTests(unittest.TestCase):
    def test_bullish_absorption_fires_buy(self):
        # range >> body, high volume, close > open
        strat = AbsorptionAgentStrategy({"min_confidence": 0.5, "min_score": 2})
        row = _base_row(
            open=100.0,
            high=110.0,
            low=99.0,
            close=100.5,  # body 0.5, range 11 > 3*0.5
            volume=200.0,
            volume_ma_20=100.0,
        )
        out = strat.evaluate(row)
        self.assertEqual(out["signal"], "BUY")
        self.assertIn("absorption", out.get("domain_scores", {}))

    def test_exhaustion_contributes_score(self):
        strat = AbsorptionAgentStrategy({
            "min_confidence": 0.4,
            "min_score": 1.5,
            "exhaustion_bars": 3,
        })
        # Three consecutive up bars with declining volume → bearish exhaustion → SELL bias
        row = _base_row(
            open=100.0,
            close=101.0,
            high=102.0,
            low=99.5,
            volume=50.0,
            volume_ma_20=100.0,
            open_shift_1=99.0,
            close_shift_1=100.0,
            volume_shift_1=80.0,
            open_shift_2=98.0,
            close_shift_2=99.0,
            volume_shift_2=120.0,
            # no absorption (body large vs range)
        )
        out = strat.evaluate(row)
        self.assertLess(out.get("domain_scores", {}).get("exhaustion", 0), 0)

    def test_confidence_gate_blocks(self):
        strat = AbsorptionAgentStrategy({"min_confidence": 0.99, "min_score": 2})
        row = _base_row(
            open=100.0,
            high=110.0,
            low=99.0,
            close=100.5,
            volume=200.0,
            volume_ma_20=100.0,
        )
        out = strat.evaluate(row)
        self.assertEqual(out["signal"], "NONE")
        self.assertIn("confidence", out.get("reject_reason", ""))

    def test_orderbook_live_imbalance(self):
        strat = AbsorptionAgentStrategy({"min_confidence": 0.4, "min_score": 1.0})
        row = _base_row(
            open=100.0,
            close=100.1,
            high=100.2,
            low=100.0,
            volume=50.0,
            volume_ma_20=100.0,
            book_imbalance=4.0,
        )
        out = strat.evaluate(row)
        self.assertGreater(out.get("domain_scores", {}).get("orderbook", 0), 0)

    def test_orderbook_from_injected_l2(self):
        strat = AbsorptionAgentStrategy({"min_confidence": 0.4, "min_score": 1.0})
        row = _base_row(
            open=100.0,
            close=100.1,
            high=100.2,
            low=100.0,
            volume=50.0,
            volume_ma_20=100.0,
            _orderbook={
                "bids": [[100.0, 400.0], [99.9, 200.0]],
                "asks": [[100.1, 50.0], [100.2, 40.0]],
            },
        )
        out = strat.evaluate(row)
        self.assertGreater(out.get("domain_scores", {}).get("orderbook", 0), 0)


if __name__ == "__main__":
    unittest.main()
