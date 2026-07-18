"""Tests for VAE regime meta-layer (filter gate, assess helper, PreTrade)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.bots.strategies_vae_regime import (
    VaeRegimeAssessment,
    assess_vae_regime_for_meta,
    vae_regime_gate_enabled,
)
from app.services.bots.strategy_filter import StrategyFilter, build_filter_from_config
from app.services.bots.strategy_runtime import apply_vae_regime_meta_gate


def _bar(**overrides):
    base = {
        "time": 1700000000,
        "open": 100,
        "high": 105,
        "low": 95,
        "close": 102,
        "volume": 1000,
        "ATR_14": 3.0,
        "RSI_14": 55.0,
        "MACDh_12_26_9": 0.5,
        "STOCHk_14_3_3": 60.0,
        "ADX_14": 28.0,
        "EMA_9": 101.0,
        "EMA_21": 100.0,
        "_symbol": "BTCUSDT",
    }
    base.update(overrides)
    return base


class TestVaeGateEnabled(unittest.TestCase):
    def test_explicit_false(self):
        self.assertFalse(vae_regime_gate_enabled({"vae_regime_gate_enabled": False}))

    def test_explicit_true(self):
        self.assertTrue(vae_regime_gate_enabled({"vae_regime_gate_enabled": True}))

    def test_auto_on_via_filter_strategy(self):
        self.assertTrue(
            vae_regime_gate_enabled({"filter_strategy": "VAE_REGIME_DETECTOR"})
        )

    def test_default_off(self):
        self.assertFalse(vae_regime_gate_enabled({}))


class TestAssessVaeRegime(unittest.TestCase):
    @patch("app.services.bots.strategies_vae_regime.get_vae_store")
    def test_suppress_above_threshold(self, mock_store_fn):
        store = MagicMock()
        store.anomaly_score.return_value = 4.0
        mock_store_fn.return_value = store

        result = assess_vae_regime_for_meta("BTCUSDT", _bar(), config={})
        self.assertEqual(result.regime_action, "suppress")
        self.assertEqual(result.regime, "unstable")
        self.assertTrue(result.model_available)

    @patch("app.services.bots.strategies_vae_regime.get_vae_store")
    def test_amplify_with_bullish_momentum(self, mock_store_fn):
        store = MagicMock()
        store.anomaly_score.return_value = 2.5
        mock_store_fn.return_value = store

        result = assess_vae_regime_for_meta(
            "BTCUSDT",
            _bar(RSI_14=65, MACDh_12_26_9=0.2),
            config={},
        )
        self.assertEqual(result.regime_action, "amplify")

    @patch("app.services.bots.strategies_vae_regime.get_vae_store")
    def test_caution_without_direction(self, mock_store_fn):
        store = MagicMock()
        store.anomaly_score.return_value = 2.5
        mock_store_fn.return_value = store

        result = assess_vae_regime_for_meta(
            "BTCUSDT",
            _bar(RSI_14=50, MACDh_12_26_9=0.0),
            config={},
        )
        self.assertEqual(result.regime_action, "caution")

    @patch("app.services.bots.strategies_vae_regime.get_vae_store")
    def test_skip_without_model(self, mock_store_fn):
        store = MagicMock()
        store.anomaly_score.return_value = None
        mock_store_fn.return_value = store

        result = assess_vae_regime_for_meta("BTCUSDT", _bar(), config={})
        self.assertEqual(result.regime_action, "skip")
        self.assertFalse(result.model_available)


class TestRegimeGateFilter(unittest.TestCase):
    def test_build_auto_regime_gate_for_vae(self):
        filt = build_filter_from_config({"filter_strategy": "VAE_REGIME_DETECTOR"})
        self.assertIsNotNone(filt)
        self.assertEqual(filt.mode, "REGIME_GATE")

    def test_regime_gate_blocks_suppress(self):
        fake = MagicMock()
        fake.evaluate.return_value = {
            "signal": "NONE",
            "regime": "unstable",
            "regime_action": "suppress",
            "anomaly_score": 4.2,
        }
        gate = StrategyFilter(fake, "REGIME_GATE")
        allowed, reason = gate.evaluate_gate(_bar(), "BUY")
        self.assertFalse(allowed)
        self.assertIn("REGIME_GATE", reason)

    def test_regime_gate_allows_normal(self):
        fake = MagicMock()
        fake.evaluate.return_value = {
            "signal": "NONE",
            "regime": "normal",
            "regime_action": "normal",
            "anomaly_score": 1.1,
        }
        gate = StrategyFilter(fake, "REGIME_GATE")
        allowed, reason = gate.evaluate_gate(_bar(), "BUY")
        self.assertTrue(allowed)


class TestApplyVaeMetaGate(unittest.TestCase):
    @patch("app.services.bots.strategies_vae_regime.assess_vae_regime_for_meta")
    def test_blocks_when_enabled_and_suppress(self, mock_assess):
        mock_assess.return_value = VaeRegimeAssessment(
            4.0, "unstable", "suppress", "unstable", True
        )
        out = apply_vae_regime_meta_gate(
            "BUY",
            row=_bar(),
            symbol="BTCUSDT",
            bot_config={"vae_regime_gate_enabled": True},
        )
        self.assertIsNone(out.signal)
        self.assertIsNotNone(out.block)
        self.assertEqual(out.block.kind, "vae_regime_gate")

    def test_passthrough_when_disabled(self):
        out = apply_vae_regime_meta_gate(
            "BUY",
            row=_bar(),
            symbol="BTCUSDT",
            bot_config={},
        )
        self.assertEqual(out.signal, "BUY")
        self.assertIsNone(out.block)

    @patch("app.services.bots.strategies_vae_regime.assess_vae_regime_for_meta")
    def test_skips_double_eval_when_vae_filter(self, mock_assess):
        out = apply_vae_regime_meta_gate(
            "BUY",
            row=_bar(),
            symbol="BTCUSDT",
            bot_config={"filter_strategy": "VAE_REGIME_DETECTOR"},
        )
        self.assertEqual(out.signal, "BUY")
        mock_assess.assert_not_called()


class TestPreTradeVaeGate(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from app.services.bots.pretrade_intel import PreTradeIntel

        self.bot_manager = MagicMock()
        self.bot_manager.oms = MagicMock()
        self.bot_manager.oms.feed = MagicMock()
        self.bot_manager.screener = MagicMock()
        self.intel = PreTradeIntel(self.bot_manager)
        self.bot = {
            "id": "bot-1",
            "symbol": "BTCUSDT",
            "strategy": "SUPERTREND_ADX",
            "timeframe": "1m",
            "config": {"vae_regime_gate_enabled": True},
        }

    @patch("app.services.bots.pretrade_intel.get_bot_candles", return_value=[])
    @patch("app.services.bots.pretrade_intel.get_aggregate_sentiment", return_value=None)
    @patch("app.services.bots.pretrade_intel.get_connection")
    @patch("app.services.bots.pretrade_intel.list_bot_exposures", return_value=[])
    @patch("app.services.bots.pretrade_intel.check_entry_gates", return_value=(True, None, None))
    @patch("app.services.bots.pretrade_intel.PRETRADE_INTEL_ENABLED", True)
    @patch("app.services.bots.strategies_vae_regime.assess_vae_regime_for_meta")
    async def test_vae_suppress_veto(
        self,
        mock_assess,
        _gates,
        _exp,
        mock_db,
        _sent,
        _candles,
    ):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        mock_assess.return_value = VaeRegimeAssessment(
            4.0, "unstable", "suppress", "VAE unstable score=4.00 > 3.5", True
        )

        verdict = await self.intel.evaluate(
            self.bot,
            "BUY",
            100.0,
            {"close": 100, "RSI_14": 50, "MACDh_12_26_9": 0, "_symbol": "BTCUSDT"},
            1783836763,
        )
        self.assertEqual(verdict["verdict"], "VETO")
        self.assertTrue(any("vae_regime_unstable" in v for v in verdict["vetoes"]))


if __name__ == "__main__":
    unittest.main()
