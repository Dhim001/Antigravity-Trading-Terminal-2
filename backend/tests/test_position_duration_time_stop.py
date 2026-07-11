"""Time-stop / position duration timestamp unit helpers."""

import unittest

from app.services.bots.position_duration import (
    as_unix_seconds,
    bars_held_since_open,
    seconds_held_since_open,
)


class AsUnixSecondsTests(unittest.TestCase):
    def test_seconds_passthrough(self):
        self.assertEqual(as_unix_seconds(1_700_000_000), 1_700_000_000.0)

    def test_milliseconds_converted(self):
        self.assertEqual(as_unix_seconds(1_700_000_000_000), 1_700_000_000.0)

    def test_invalid(self):
        self.assertIsNone(as_unix_seconds(None))
        self.assertIsNone(as_unix_seconds(0))
        self.assertIsNone(as_unix_seconds(-1))


class BarsHeldSinceOpenTests(unittest.TestCase):
    def test_five_minute_bars_on_1m(self):
        opened = 1_700_000_000
        bar = opened + 5 * 60
        self.assertAlmostEqual(bars_held_since_open(opened, bar, "1m"), 5.0)

    def test_does_not_treat_seconds_as_ms(self):
        # Regression: dividing (sec - sec) by tf_ms made elapsed ~1000x too small.
        opened = 1_700_000_000
        bar = opened + 600  # 10 minutes
        self.assertAlmostEqual(bars_held_since_open(opened, bar, "1m"), 10.0)
        self.assertLess(bars_held_since_open(opened, bar, "1m"), 100.0)

    def test_accepts_ms_bar_time(self):
        opened = 1_700_000_000
        bar_ms = (opened + 300) * 1000
        self.assertAlmostEqual(bars_held_since_open(opened, bar_ms, "1m"), 5.0)


class SecondsHeldSinceOpenTests(unittest.TestCase):
    def test_tick_ms_minus_opened_seconds(self):
        opened = 1_700_000_000.0
        time_ms = int((opened + 45) * 1000)
        self.assertAlmostEqual(seconds_held_since_open(opened, time_ms), 45.0)

    def test_regression_mixed_units_do_not_inflate(self):
        # Old bug: (time_ms - opened_at) / 1000 ≈ years immediately after open.
        opened = 1_700_000_000.0
        time_ms = int(opened * 1000) + 1_000  # 1s later
        elapsed = seconds_held_since_open(opened, time_ms)
        self.assertAlmostEqual(elapsed, 1.0)
        self.assertLess(elapsed, 10.0)


if __name__ == "__main__":
    unittest.main()
