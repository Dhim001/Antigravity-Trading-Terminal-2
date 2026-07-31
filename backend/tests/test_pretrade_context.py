"""Tests for Pre-Trade streak adaptive policy helpers."""

from __future__ import annotations

import unittest

from app.services.bots.pretrade_context import (
    apply_failures_streak,
    apply_reduce_size_multiplier,
    prefer_hold_on_streak,
    set_bot_streak_cooldown,
    suggest_streak_thresholds_from_backtest,
)


class TestApplyFailuresStreak(unittest.TestCase):
    def test_default_reduce(self):
        out = apply_failures_streak(
            [-10, -20, -30],
            bot_config={},
            setup_fail_limit=3,
            newest_first=False,
        )
        self.assertIsNotNone(out)
        self.assertEqual(out["verdict"], "REDUCE_SIZE")
        self.assertAlmostEqual(out["size_multiplier"], 0.5)

    def test_severe_step(self):
        out = apply_failures_streak(
            [-1, -1, -1, -1, -1],
            bot_config={
                "pretrade_streak_severe_limit": 5,
                "max_consecutive_losses": 10,  # Sentinel pause above severe cut
            },
            setup_fail_limit=3,
            newest_first=False,
        )
        self.assertEqual(out["verdict"], "REDUCE_SIZE")
        self.assertAlmostEqual(out["size_multiplier"], 0.25)

    def test_veto_mode(self):
        out = apply_failures_streak(
            [-10, -20, -30],
            bot_config={"pretrade_streak_mode": "veto"},
            setup_fail_limit=3,
            newest_first=False,
        )
        self.assertEqual(out["verdict"], "VETO")

    def test_off_mode(self):
        out = apply_failures_streak(
            [-10, -20, -30],
            bot_config={"pretrade_streak_mode": "off"},
            setup_fail_limit=3,
            newest_first=False,
        )
        self.assertIsNone(out)

    def test_prefer_hold_only_when_cool_active(self):
        # Streak alone must not hard-HOLD (would permanently freeze entries).
        sig, data = prefer_hold_on_streak(
            "BUY",
            {"signal": "BUY"},
            bot_config={"pretrade_aware_signals": True},
            consecutive_losses=5,
            cool_until_ts=None,
        )
        self.assertEqual(sig, "BUY")

        sig2, data2 = prefer_hold_on_streak(
            "BUY",
            {"signal": "BUY"},
            bot_config={"pretrade_aware_signals": True},
            consecutive_losses=3,
            cool_until_ts=9_999_999_999.0,
        )
        self.assertIsNone(sig2)
        self.assertEqual(data2["reject_reason"], "pretrade_streak_aware")

    def test_suggest_from_backtest(self):
        sug = suggest_streak_thresholds_from_backtest({"max_consecutive_losses": 8})
        self.assertTrue(sug["ok"])
        self.assertEqual(sug["suggested"]["pretrade_streak_mode"], "reduce")
        self.assertGreaterEqual(sug["suggested"]["max_consecutive_losses"], 8)

    def test_cooldown_arm(self):
        bot = {}
        until = set_bot_streak_cooldown(bot, 60, now=1_000.0)
        self.assertEqual(until, 1_060.0)
        self.assertEqual(bot["_pretrade_streak_cool_until"], 1_060.0)

    def test_reduce_size_dedupes_regime_halve(self):
        qty, note = apply_reduce_size_multiplier(
            100.0,
            0.5,
            vetoes=["failures_streak: 3 losses"],
            recent_closed_pnls=[-1, -1, -1],
            use_regime_sizing=True,
        )
        self.assertAlmostEqual(qty, 100.0)
        self.assertIn("align", note)

        qty2, _ = apply_reduce_size_multiplier(
            100.0,
            0.25,
            vetoes=["failures_streak: 5 losses"],
            recent_closed_pnls=[-1, -1, -1],
            use_regime_sizing=True,
        )
        self.assertAlmostEqual(qty2, 50.0)

        qty3, _ = apply_reduce_size_multiplier(
            100.0,
            0.5,
            vetoes=["sentiment_divergence: score=-0.9"],
            recent_closed_pnls=[-1, -1, -1],
            use_regime_sizing=True,
        )
        self.assertAlmostEqual(qty3, 50.0)


if __name__ == "__main__":
    unittest.main()
