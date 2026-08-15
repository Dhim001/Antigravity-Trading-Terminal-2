"""Tests for Pre-Trade streak adaptive policy helpers."""

from __future__ import annotations

import time
import unittest
from unittest import mock

from app.services.bots.pretrade_context import (
    apply_failures_streak,
    apply_reduce_size_multiplier,
    clear_bot_streak_cooldown,
    filter_exit_pnls_by_lookback,
    get_bot_streak_cooldown_hold,
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

    def test_veto_after_max_consecutive_losses(self):
        """Sentinel max escalates reduce-mode streak to hard VETO."""
        out = apply_failures_streak(
            [-1, -1, -1, -1, -1],
            bot_config={
                "pretrade_streak_mode": "reduce",
                "max_consecutive_losses": 5,
            },
            setup_fail_limit=3,
            newest_first=False,
        )
        self.assertEqual(out["verdict"], "VETO")
        self.assertEqual(out["streak"], 5)

    def test_bar_time_lookback_clears_old_losses(self):
        """Losses outside PRETRADE_SETUP_LOOKBACK_HOURS no longer keep VETO latched."""
        now = 1_000_000.0
        hours = 24.0
        old = now - (hours + 1) * 3600.0
        recent = now - 3600.0
        # Five old losses would VETO without lookback; all outside window → None.
        out = apply_failures_streak(
            [-1, -1, -1, -1, -1],
            bot_config={
                "pretrade_streak_mode": "veto",
                "max_consecutive_losses": 5,
                "pretrade_setup_lookback_hours": hours,
            },
            setup_fail_limit=3,
            newest_first=False,
            exit_times=[old, old + 1, old + 2, old + 3, old + 4],
            now_ts=now,
        )
        self.assertIsNone(out)

        # Mix: old losses + 2 recent → streak of 2 < fail_limit → no action.
        out2 = apply_failures_streak(
            [-1, -1, -1, -1, -1],
            bot_config={
                "pretrade_streak_mode": "veto",
                "max_consecutive_losses": 5,
                "pretrade_setup_lookback_hours": hours,
            },
            setup_fail_limit=3,
            newest_first=False,
            exit_times=[old, old + 1, old + 2, recent - 10, recent],
            now_ts=now,
        )
        self.assertIsNone(out2)

        # Five recent losses still VETO.
        out3 = apply_failures_streak(
            [-1, -1, -1, -1, -1],
            bot_config={
                "pretrade_streak_mode": "veto",
                "max_consecutive_losses": 5,
                "pretrade_setup_lookback_hours": hours,
            },
            setup_fail_limit=3,
            newest_first=False,
            exit_times=[recent - 40, recent - 30, recent - 20, recent - 10, recent],
            now_ts=now,
        )
        self.assertEqual(out3["verdict"], "VETO")

    def test_resume_after_lookback_decay(self):
        """After cool-down / window decay, streak below threshold allows entries."""
        now = 2_000_000.0
        hours = 24.0
        # At T0: 5 losses inside window → VETO.
        t0 = now - 2 * 3600.0
        losses_t = [t0 - 400, t0 - 300, t0 - 200, t0 - 100, t0]
        pnls = [-1.0] * 5
        veto = apply_failures_streak(
            pnls,
            bot_config={
                "pretrade_streak_mode": "veto",
                "max_consecutive_losses": 5,
                "pretrade_setup_lookback_hours": hours,
            },
            setup_fail_limit=3,
            newest_first=False,
            exit_times=losses_t,
            now_ts=t0,
        )
        self.assertEqual(veto["verdict"], "VETO")
        self.assertGreater(veto.get("cooldown_sec", 0), 0)

        # After lookback: all exits aged out of the window → unlocked.
        later = losses_t[-1] + hours * 3600.0 + 60.0
        resumed = apply_failures_streak(
            pnls,
            bot_config={
                "pretrade_streak_mode": "veto",
                "max_consecutive_losses": 5,
                "pretrade_setup_lookback_hours": hours,
            },
            setup_fail_limit=3,
            newest_first=False,
            exit_times=losses_t,
            now_ts=later,
        )
        self.assertIsNone(resumed)

    def test_filter_exit_pnls_by_lookback(self):
        now = 10_000.0
        filtered = filter_exit_pnls_by_lookback(
            [-1, -2, 3],
            exit_times=[now - 100_000, now - 10, now - 5],
            now_ts=now,
            lookback_hours=1.0,
        )
        self.assertEqual(filtered, [-2.0, 3.0])

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

    def test_clear_cooldown(self):
        bot = {"_pretrade_streak_cool_until": 9_999.0, "_pretrade_streak_count": 5}
        clear_bot_streak_cooldown(bot)
        self.assertNotIn("_pretrade_streak_cool_until", bot)
        self.assertEqual(bot["_pretrade_streak_count"], 0)

    @mock.patch(
        "app.services.bots.analytics.get_recent_consecutive_losses",
        return_value=1,
    )
    def test_hold_clears_when_bot_streak_below_limit(self, _mock_losses):
        """One-exit bot must not keep a cool-down armed from a false 5-loss streak."""
        bot = {
            "id": "bot-ada-rl",
            "config": {"pretrade_setup_fail_limit": 3},
            "_pretrade_streak_cool_until": time.time() + 500,
            "_pretrade_streak_count": 5,
        }
        self.assertIsNone(get_bot_streak_cooldown_hold(bot))
        self.assertNotIn("_pretrade_streak_cool_until", bot)

    @mock.patch(
        "app.services.bots.analytics.get_recent_consecutive_losses",
        return_value=4,
    )
    def test_hold_keeps_when_bot_streak_still_hot(self, _mock_losses):
        until = time.time() + 500
        bot = {
            "id": "bot-hot",
            "config": {"pretrade_setup_fail_limit": 3},
            "_pretrade_streak_cool_until": until,
            "_pretrade_streak_count": 3,
        }
        hold = get_bot_streak_cooldown_hold(bot)
        self.assertIsNotNone(hold)
        self.assertEqual(hold["kind"], "pretrade_streak")
        self.assertEqual(hold["consecutive_losses"], 4)

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
