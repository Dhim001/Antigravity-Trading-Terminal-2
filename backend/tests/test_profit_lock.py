"""Armed chandelier / profit-floor ratchet."""

import unittest

from app.services.bots.profit_lock import (
    hold_excursion_from_candles,
    profit_r_units,
    ratchet_chandelier_stop,
    resolve_r_distance,
)
from app.services.bots.positions import evaluate_risk_trigger, sl_tp_limit_fill_price


class ResolveRDistanceTests(unittest.TestCase):
    def test_prefers_percent_stop(self):
        dist = resolve_r_distance(
            avg_price=93.43975,
            stop_loss_percent=2.0,
            entry_atr=0.21,
            chandelier_multiplier=2.0,
        )
        self.assertAlmostEqual(dist, 93.43975 * 0.02, places=6)

    def test_falls_back_to_atr(self):
        dist = resolve_r_distance(
            avg_price=100.0,
            stop_loss_percent=None,
            entry_atr=2.0,
            chandelier_multiplier=1.5,
        )
        self.assertAlmostEqual(dist, 3.0)


class ArmedChandelierSolPathTests(unittest.TestCase):
    """Replay the 22 Aug SOL give-back geometry."""

    ENTRY = 93.43975
    PEAK = 95.51
    ATR = 0.21
    INIT_SL = 91.570955

    def test_unarmed_keeps_wide_initial_stop(self):
        # +1.25% high (94.61) is still under 1R of the 2% stop.
        sl = ratchet_chandelier_stop(
            is_long=True,
            avg_price=self.ENTRY,
            extreme=94.61,
            atr=self.ATR,
            current_sl=self.INIT_SL,
            multiplier=2.0,
            arm_r=1.0,
            stop_loss_percent=2.0,
            entry_atr=self.ATR,
        )
        self.assertAlmostEqual(sl, self.INIT_SL)
        self.assertLess(
            profit_r_units(
                is_long=True,
                avg_price=self.ENTRY,
                extreme=94.61,
                r_distance=self.ENTRY * 0.02,
            ),
            1.0,
        )

    def test_armed_at_peak_trails_two_atr_not_two_percent(self):
        sl = ratchet_chandelier_stop(
            is_long=True,
            avg_price=self.ENTRY,
            extreme=self.PEAK,
            atr=self.ATR,
            current_sl=self.INIT_SL,
            multiplier=2.0,
            arm_r=1.0,
            stop_loss_percent=2.0,
            entry_atr=self.ATR,
        )
        # 95.51 - 2×0.21 = 95.09, well above the live 93.60 percent trail.
        self.assertAlmostEqual(sl, self.PEAK - 2.0 * self.ATR, places=5)
        self.assertGreater(sl, 94.5)

    def test_legacy_immediate_trail_unchanged(self):
        sl = ratchet_chandelier_stop(
            is_long=True,
            avg_price=100.0,
            extreme=101.0,
            atr=2.0,
            current_sl=None,
            multiplier=3.0,
            arm_r=0.0,
        )
        self.assertEqual(sl, 95.0)


class EvaluateRiskTriggerArmTests(unittest.TestCase):
    def test_percent_path_still_trails_from_last_when_chandelier_off(self):
        trigger, sl, hi, _lo = evaluate_risk_trigger(
            1.0,
            100.0,
            102.0,
            stop_loss_percent=2.0,
            take_profit_percent=2.5,
            stop_loss_price=98.0,
            take_profit_price=102.5,
            chandelier_stop_enabled=False,
            high_watermark=100.0,
            low_watermark=100.0,
        )
        self.assertIsNone(trigger)
        self.assertAlmostEqual(hi, 102.0)
        self.assertAlmostEqual(sl, 99.96)

    def test_armed_chandelier_does_not_fire_on_early_dip(self):
        trigger, sl, _hi, _lo = evaluate_risk_trigger(
            1.0,
            93.43975,
            92.56,
            stop_loss_percent=2.0,
            take_profit_percent=2.5,
            stop_loss_price=91.570955,
            take_profit_price=95.735,
            chandelier_stop_enabled=True,
            chandelier_multiplier=2.0,
            chandelier_arm_r=1.0,
            high_watermark=94.11,
            low_watermark=92.56,
            entry_atr=0.21,
            current_atr=0.21,
        )
        self.assertIsNone(trigger)
        self.assertAlmostEqual(sl, 91.570955)

    def test_armed_chandelier_exits_near_peak_trail(self):
        trigger, sl, _hi, _lo = evaluate_risk_trigger(
            1.0,
            93.43975,
            94.90,
            stop_loss_percent=2.0,
            take_profit_percent=2.5,
            stop_loss_price=91.570955,
            take_profit_price=95.735,
            chandelier_stop_enabled=True,
            chandelier_multiplier=2.0,
            chandelier_arm_r=1.0,
            high_watermark=95.51,
            low_watermark=93.26,
            entry_atr=0.21,
            current_atr=0.21,
        )
        self.assertEqual(trigger, "SL")
        self.assertAlmostEqual(sl, 95.51 - 0.42, places=5)
        fill = sl_tp_limit_fill_price(
            trigger,
            market_price=94.90,
            stop_loss_price=sl,
            previous_stop_loss_price=91.570955,
            size=1.0,
        )
        self.assertEqual(fill, 94.90)

    def test_wick_high_arms_when_last_is_below_one_r(self):
        last = 94.80  # +1.46% — under the 2% / 1R arm
        trigger, sl, hi, _lo = evaluate_risk_trigger(
            1.0,
            93.43975,
            last,
            stop_loss_percent=2.0,
            take_profit_percent=2.5,
            stop_loss_price=91.570955,
            take_profit_price=95.735,
            chandelier_stop_enabled=True,
            chandelier_multiplier=2.0,
            chandelier_arm_r=1.0,
            high_watermark=94.80,
            low_watermark=93.26,
            entry_atr=0.21,
            current_atr=0.21,
            bar_high=95.51,
            bar_low=93.26,
        )
        self.assertAlmostEqual(hi, 95.51)
        self.assertEqual(trigger, "SL")
        self.assertAlmostEqual(sl, 95.51 - 0.42, places=5)

    def test_recycle_does_not_fill_invented_stop_above_last(self):
        trigger, sl, _hi, _lo = evaluate_risk_trigger(
            1.0,
            93.43975,
            93.60,
            stop_loss_percent=2.0,
            take_profit_percent=2.5,
            stop_loss_price=93.60,
            take_profit_price=95.735,
            chandelier_stop_enabled=True,
            chandelier_multiplier=2.0,
            chandelier_arm_r=1.0,
            high_watermark=95.51,
            low_watermark=93.26,
            entry_atr=0.21,
            current_atr=0.21,
        )
        self.assertEqual(trigger, "SL")
        fill = sl_tp_limit_fill_price(
            trigger,
            market_price=93.60,
            stop_loss_price=sl,
            previous_stop_loss_price=93.60,
            size=21.4,
        )
        self.assertEqual(fill, 93.60)
        self.assertGreater(sl, 94.5)


class HoldExcursionTests(unittest.TestCase):
    def test_skips_bars_before_open(self):
        candles = [
            {"time": 1_000, "high": 99.0, "low": 90.0},
            {"time": 2_000, "high": 95.51, "low": 93.26},
            {"time": 2_060, "high": 94.2, "low": 93.5},
        ]
        hi, lo = hold_excursion_from_candles(candles, opened_at=1_950)
        self.assertAlmostEqual(hi, 95.51)
        self.assertAlmostEqual(lo, 93.26)

    def test_keeps_opening_bar(self):
        candles = [
            {"time": 1_900, "high": 94.0, "low": 92.5},
            {"time": 1_960, "high": 95.51, "low": 93.0},
        ]
        hi, lo = hold_excursion_from_candles(candles, opened_at=1_930)
        self.assertAlmostEqual(hi, 95.51)
        self.assertAlmostEqual(lo, 92.5)


if __name__ == "__main__":
    unittest.main()
