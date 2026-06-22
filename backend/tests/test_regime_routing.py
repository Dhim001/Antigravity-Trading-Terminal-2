"""Tests for ATR regime routing."""

import unittest

from app.services.agent.regime_routing import resolve_regime_config


def _insight(regime="normal", confidence=0.7, score=3):
    return {
        "confidence": confidence,
        "score": score,
        "sub_reports": {"risk": {"atr_regime": regime}},
    }


class TestRegimeRouting(unittest.TestCase):
    def test_disabled_returns_original_config(self):
        cfg = {"min_confidence": 0.55, "regime_routing_enabled": False}
        effective, regime = resolve_regime_config(cfg, _insight("elevated"))
        self.assertIsNone(regime)
        self.assertEqual(effective["min_confidence"], 0.55)

    def test_elevated_raises_min_confidence(self):
        cfg = {
            "min_confidence": 0.55,
            "regime_routing_enabled": True,
            "elevated_min_confidence": 0.65,
        }
        effective, regime = resolve_regime_config(cfg, _insight("elevated"))
        self.assertEqual(regime, "elevated")
        self.assertGreaterEqual(effective["min_confidence"], 0.65)

    def test_elevated_raises_min_score(self):
        cfg = {
            "min_score": 2,
            "regime_routing_enabled": True,
            "elevated_min_score": 4,
        }
        effective, _ = resolve_regime_config(cfg, _insight("elevated"))
        self.assertEqual(effective["min_score"], 4)

    def test_normal_regime_unchanged_when_no_overrides(self):
        cfg = {"min_confidence": 0.55, "regime_routing_enabled": True}
        effective, regime = resolve_regime_config(cfg, _insight("normal"))
        self.assertEqual(regime, "normal")
        self.assertEqual(effective["min_confidence"], 0.55)

    def test_build_signal_uses_elevated_threshold(self):
        from app.services.bots.strategies_chart_agent import build_signal_from_insight

        insight = {
            "signal": "BUY",
            "confidence": 0.6,
            "score": 3,
            "sub_reports": {"risk": {"atr_regime": "elevated"}},
        }
        cfg = {
            "min_confidence": 0.55,
            "regime_routing_enabled": True,
            "elevated_min_confidence": 0.65,
        }
        out = build_signal_from_insight(insight, cfg)
        self.assertEqual(out["signal"], "NONE")
        self.assertIn("confidence", out.get("reject_reason", "").lower())


if __name__ == "__main__":
    unittest.main()
