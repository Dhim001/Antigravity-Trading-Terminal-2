"""Parity helpers: shared gates, sizing, deterministic pretrade."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from app.services.bots.strategy_runtime import (
    apply_shared_signal_gates,
    evaluate_parity_pretrade,
    scale_entry_quantity,
)


class TestSharedSignalGates(unittest.TestCase):
    def test_noop_when_flags_off(self):
        data = {"signal": "BUY", "confidence": 0.8}
        out = apply_shared_signal_gates("BUY", data, bot_config={})
        self.assertEqual(out.signal, "BUY")
        self.assertIsNone(out.block)

    def test_conformal_can_block(self):
        data = {"signal": "BUY", "confidence": 0.2}
        cfg = {
            "conformal_gate_enabled": True,
            "_bot_id": "bot-x",
            "min_confidence": 0.55,
        }
        with patch(
            "app.services.bots.conformal_gate.load_conformal",
            return_value=None,
        ):
            out = apply_shared_signal_gates("BUY", data, bot_config=cfg)
        self.assertIsNone(out.signal)
        self.assertIsNotNone(out.block)
        self.assertEqual(out.block.kind, "conformal_gate")

    def test_hmm_scales_confidence(self):
        from app.services.bots import hmm_regime as hr

        model = hr.RegimeModel(
            means=((0.001, 0.01),),
            covariances=(np.eye(2) * 0.0001,),
            weights=(1.0,),
            state_labels=("bull_quiet",),
            vol_threshold=0.02,
        )
        data = {"signal": "BUY", "confidence": 0.7}
        cfg = {"hmm_regime_gate_enabled": True, "_bot_id": "b1"}
        with patch.object(hr, "load_regime_model", return_value=model):
            out = apply_shared_signal_gates(
                "BUY",
                data,
                bot_config=cfg,
                recent_features=np.array([[0.001, 0.01]]),
            )
        self.assertEqual(out.signal, "BUY")
        self.assertGreater(out.signal_data["confidence"], 0.7)


class TestScaleEntryQuantity(unittest.TestCase):
    def test_confidence_sizing_default_on(self):
        qty = scale_entry_quantity(
            10.0,
            signal_data={"confidence": 1.0},
            bot_config={},
        )
        # conf=1.0 → scale 0.7+0.6=1.3
        self.assertAlmostEqual(qty, 13.0, places=4)

    def test_confidence_sizing_can_disable(self):
        qty = scale_entry_quantity(
            10.0,
            signal_data={"confidence": 1.0},
            bot_config={"use_confidence_sizing": False},
        )
        self.assertAlmostEqual(qty, 10.0, places=4)

    def test_regime_halving_on_three_losses(self):
        qty = scale_entry_quantity(
            10.0,
            signal_data={"confidence": 0.55},
            bot_config={"use_confidence_sizing": False, "use_regime_sizing": True},
            recent_closed_pnls=[-1.0, -2.0, -3.0],
        )
        self.assertAlmostEqual(qty, 5.0, places=4)


class TestParityPretrade(unittest.TestCase):
    def test_failures_streak_reduce_by_default(self):
        out = evaluate_parity_pretrade(
            side="BUY",
            symbol="AAPL",
            bar_time=1,
            bot_config={},
            recent_exit_pnls=[-10, -20, -30],
            setup_fail_limit=3,
        )
        self.assertEqual(out["verdict"], "REDUCE_SIZE")
        self.assertTrue(any("failures_streak" in v for v in out["vetoes"]))
        self.assertAlmostEqual(out["size_multiplier"], 0.5)

    def test_failures_streak_veto_mode(self):
        out = evaluate_parity_pretrade(
            side="BUY",
            symbol="AAPL",
            bar_time=1,
            bot_config={"pretrade_streak_mode": "veto"},
            recent_exit_pnls=[-10, -20, -30],
            setup_fail_limit=3,
        )
        self.assertEqual(out["verdict"], "VETO")
        self.assertTrue(any("failures_streak" in v for v in out["vetoes"]))

    def test_gap_anomaly_veto(self):
        out = evaluate_parity_pretrade(
            side="BUY",
            symbol="AAPL",
            bar_time=1,
            bot_config={},
            anomaly={"is_anomaly": True, "kinds": ["price_gap"], "gap_pct": 5.0},
            gap_veto_pct=3.0,
        )
        self.assertEqual(out["verdict"], "VETO")

    def test_sentiment_reduce(self):
        out = evaluate_parity_pretrade(
            side="BUY",
            symbol="AAPL",
            bar_time=1,
            bot_config={},
            sentiment={"score": -0.8, "mentions": 10},
            sentiment_threshold=0.5,
            sentiment_min_mentions=3,
            reduce_size_factor=0.5,
        )
        self.assertEqual(out["verdict"], "REDUCE_SIZE")
        self.assertEqual(out["size_multiplier"], 0.5)

    def test_sentiment_accepts_store_key_aliases(self):
        out = evaluate_parity_pretrade(
            side="BUY",
            symbol="AAPL",
            bar_time=1,
            bot_config={},
            sentiment={"aggregate_score": -0.8, "mention_count": 10},
            sentiment_threshold=0.5,
            sentiment_min_mentions=3,
            reduce_size_factor=0.5,
        )
        self.assertEqual(out["verdict"], "REDUCE_SIZE")


if __name__ == "__main__":
    unittest.main()
