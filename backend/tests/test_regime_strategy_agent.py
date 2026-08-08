"""Tests for REGIME_STRATEGY_AGENT + shared ATR/ADX classify."""

from __future__ import annotations

import unittest

from app.services.bots.indicators import adx_col, atr_col
from app.services.bots.regime_classify import (
    DEFAULT_REGIME_STRATEGY_MAP,
    classify_atr_adx_regime,
)
from app.services.bots.strategies import get_strategy
from app.services.bots.strategies_regime_agent import RegimeStrategyAgent


def _row(*, atr=10.0, median=10.0, adx=20.0, atr_len=14, adx_len=14):
    return {
        atr_col(atr_len): atr,
        f"{atr_col(atr_len)}_median_20": median,
        adx_col(adx_len): adx,
    }


class RegimeClassifyTests(unittest.TestCase):
    def test_elevated_vol(self):
        self.assertEqual(
            classify_atr_adx_regime(_row(atr=20.0, median=10.0, adx=40.0)),
            "elevated_vol",
        )

    def test_trending(self):
        self.assertEqual(
            classify_atr_adx_regime(_row(atr=10.0, median=10.0, adx=30.0)),
            "trending",
        )

    def test_ranging(self):
        self.assertEqual(
            classify_atr_adx_regime(_row(atr=10.0, median=10.0, adx=20.0)),
            "ranging",
        )

    def test_map_defaults(self):
        self.assertEqual(DEFAULT_REGIME_STRATEGY_MAP["elevated_vol"], "VWAP_PULLBACK")
        self.assertEqual(DEFAULT_REGIME_STRATEGY_MAP["trending"], "SUPERTREND_ADX")
        self.assertEqual(DEFAULT_REGIME_STRATEGY_MAP["ranging"], "BRS_SCALPING")


class StubChild:
    def __init__(self, signal="BUY", name="STUB"):
        self.signal = signal
        self.name = name
        self.calls = 0

    def evaluate(self, df_row):
        self.calls += 1
        return {"signal": self.signal, "reasons": [f"from-{self.name}"]}


class RegimeStrategyAgentTests(unittest.TestCase):
    def test_get_strategy_factory(self):
        strat = get_strategy("REGIME_STRATEGY_AGENT", {"regime_hysteresis_bars": 2})
        self.assertIsInstance(strat, RegimeStrategyAgent)

    def test_hysteresis_requires_n_bars(self):
        agent = RegimeStrategyAgent({
            "regime_hysteresis_bars": 3,
            "regime_min_hold_bars": 0,
        })
        # Seed ranging
        agent._children = {
            "BRS_SCALPING": StubChild("BUY", "BRS"),
            "SUPERTREND_ADX": StubChild("SELL", "ST"),
            "VWAP_PULLBACK": StubChild("NONE", "VWAP"),
        }
        r1 = agent.evaluate(_row(adx=20))
        self.assertEqual(r1["selected_strategy"], "BRS_SCALPING")
        self.assertEqual(r1["regime"], "ranging")

        # Propose trending for 2 bars — not enough
        for _ in range(2):
            out = agent.evaluate(_row(adx=30))
            self.assertEqual(out["regime"], "ranging")
            self.assertEqual(out["selected_strategy"], "BRS_SCALPING")

        # 3rd consecutive trending flips
        out = agent.evaluate(_row(adx=30))
        self.assertEqual(out["regime"], "trending")
        self.assertEqual(out["selected_strategy"], "SUPERTREND_ADX")
        self.assertTrue(out["regime_switched"])

    def test_min_hold_blocks_flip(self):
        agent = RegimeStrategyAgent({
            "regime_hysteresis_bars": 1,
            "regime_min_hold_bars": 5,
        })
        agent._children = {
            "BRS_SCALPING": StubChild("BUY", "BRS"),
            "SUPERTREND_ADX": StubChild("SELL", "ST"),
            "VWAP_PULLBACK": StubChild("NONE", "VWAP"),
        }
        agent.evaluate(_row(adx=20))  # ranging
        # Even with hysteresis=1, min_hold blocks
        for _ in range(4):
            out = agent.evaluate(_row(adx=30))
            self.assertEqual(out["regime"], "ranging")
        out = agent.evaluate(_row(adx=30))
        self.assertEqual(out["regime"], "trending")

    def test_state_isolation_across_instances(self):
        a = RegimeStrategyAgent({"regime_hysteresis_bars": 1, "regime_min_hold_bars": 0})
        b = RegimeStrategyAgent({"regime_hysteresis_bars": 1, "regime_min_hold_bars": 0})
        for agent in (a, b):
            agent._children = {
                "BRS_SCALPING": StubChild("BUY", "BRS"),
                "SUPERTREND_ADX": StubChild("SELL", "ST"),
                "VWAP_PULLBACK": StubChild("NONE", "VWAP"),
            }
        a.evaluate(_row(adx=30))
        self.assertEqual(a._active_regime, "trending")
        self.assertIsNone(b._active_regime)

    def test_recursion_blocked_for_self(self):
        agent = RegimeStrategyAgent({
            "regime_strategy_map": {"ranging": "REGIME_STRATEGY_AGENT"},
            "regime_hysteresis_bars": 1,
            "regime_min_hold_bars": 0,
        })
        # Self-map is clamped to BRS fallback — still evaluates (not unavailable).
        out = agent.evaluate(_row(adx=10))
        self.assertEqual(out["selected_strategy"], "BRS_SCALPING")
        self.assertEqual(out["regime"], "ranging")

    def test_init_is_not_regime_switched(self):
        agent = RegimeStrategyAgent({
            "regime_hysteresis_bars": 1,
            "regime_min_hold_bars": 0,
        })
        agent._children = {
            "BRS_SCALPING": StubChild("BUY", "BRS"),
            "SUPERTREND_ADX": StubChild("SELL", "ST"),
            "VWAP_PULLBACK": StubChild("NONE", "VWAP"),
        }
        out = agent.evaluate(_row(adx=20))
        self.assertFalse(out["regime_switched"])
        self.assertEqual(out["regime"], "ranging")

    def test_vwap_missing_falls_back_to_brs(self):
        agent = RegimeStrategyAgent({
            "regime_hysteresis_bars": 1,
            "regime_min_hold_bars": 0,
        })
        brs = StubChild("BUY", "BRS")
        agent._children = {
            "BRS_SCALPING": brs,
            "SUPERTREND_ADX": StubChild("SELL", "ST"),
            "VWAP_PULLBACK": StubChild("NONE", "VWAP"),
        }
        # elevated_vol → VWAP, but VWAP column missing → BRS
        out = agent.evaluate(_row(atr=20.0, median=10.0, adx=40.0))
        self.assertEqual(out["selected_strategy"], "BRS_SCALPING")
        self.assertEqual(out["signal"], "BUY")
        self.assertEqual(brs.calls, 1)

    def test_map_override_outside_allowlist_clamped(self):
        agent = RegimeStrategyAgent({
            "regime_strategy_map": {"ranging": "MACD_RSI"},
            "regime_hysteresis_bars": 1,
            "regime_min_hold_bars": 0,
        })
        agent._children = {
            "BRS_SCALPING": StubChild("BUY", "BRS"),
            "SUPERTREND_ADX": StubChild("SELL", "ST"),
            "VWAP_PULLBACK": StubChild("NONE", "VWAP"),
        }
        out = agent.evaluate(_row(adx=10))
        self.assertEqual(out["selected_strategy"], "BRS_SCALPING")

    def test_screener_regime_union_computes(self):
        """Regression: REGIME indicator union must not UnboundLocalError."""
        import numpy as np
        import pandas as pd

        from app.services.bots.indicators import merge_strategy_config
        from app.services.bots.screener import MarketScreenerService

        n = 120
        close = np.linspace(100, 110, n)
        df = pd.DataFrame({
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(n, 1000.0),
            "time": [1_700_000_000 + i * 300 for i in range(n)],
        })
        cfg = merge_strategy_config("REGIME_STRATEGY_AGENT", {})
        screener = MarketScreenerService()
        screener._ensure_atr(df, cfg)
        screener._compute_for_strategy(df, "REGIME_STRATEGY_AGENT", cfg)
        self.assertIn("VWAP", df.columns)
        self.assertFalse(df["VWAP"].isna().all())
        # Supertrend direction column from child defaults (st_length=14, mult=3)
        st_cols = [c for c in df.columns if str(c).startswith("SUPERTd_")]
        self.assertTrue(st_cols, msg=f"missing SUPERTd_*; cols={list(df.columns)}")


if __name__ == "__main__":
    unittest.main()
