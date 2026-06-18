"""Tests for multi-timeframe bot bar-close routing."""

import unittest

from app.services.bots.bar_events import BarCloseTracker
from app.services.bots.candle_source import candles_for_timeframe, get_bot_candles


def _bar(time_sec: int, close: float) -> dict:
    return {
        "time": time_sec,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1.0,
    }


def _one_minute_series(base: int, count: int) -> list[dict]:
    return [_bar(base + i * 60, 100.0 + i) for i in range(count)]


class BarCloseTrackerTimeframeTests(unittest.TestCase):
    def test_1m_and_5m_trackers_are_independent(self):
        tracker = BarCloseTracker()
        base = (1_700_000_000 // 300) * 300

        series_1m = _one_minute_series(base, 2)
        self.assertFalse(tracker.check("BTCUSDT", series_1m, timeframe="1m"))
        series_1m = _one_minute_series(base, 3)
        self.assertTrue(tracker.check("BTCUSDT", series_1m, timeframe="1m"))

        series_5m = candles_for_timeframe(_one_minute_series(base, 5), "5m")
        self.assertFalse(tracker.check("BTCUSDT", series_5m, timeframe="5m"))
        warm_5m = candles_for_timeframe(_one_minute_series(base, 10), "5m")
        self.assertFalse(tracker.check("BTCUSDT", warm_5m, timeframe="5m"))
        extended_5m = candles_for_timeframe(_one_minute_series(base, 15), "5m")
        self.assertTrue(tracker.check("BTCUSDT", extended_5m, timeframe="5m"))

    def test_same_1m_advance_does_not_fire_5m_until_bucket_closes(self):
        tracker = BarCloseTracker()
        base = (1_700_000_000 // 300) * 300

        for count in range(2, 6):
            series = candles_for_timeframe(_one_minute_series(base, count), "5m")
            self.assertFalse(tracker.check("ETHUSDT", series, timeframe="5m"))

        warm = candles_for_timeframe(_one_minute_series(base, 10), "5m")
        self.assertFalse(tracker.check("ETHUSDT", warm, timeframe="5m"))
        extended = candles_for_timeframe(_one_minute_series(base, 15), "5m")
        self.assertTrue(tracker.check("ETHUSDT", extended, timeframe="5m"))


class BotCandleTimeframeTests(unittest.TestCase):
    def test_resample_from_stub_feed(self):
        base = (1_700_000_000 // 300) * 300
        live = _one_minute_series(base, 300)

        class _Feed:
            def get_candles(self, symbol):
                return live

        out_1m = get_bot_candles("AAPL", _Feed(), timeframe="1m", min_bars=200)
        out_5m = get_bot_candles("AAPL", _Feed(), timeframe="5m", min_bars=50)
        self.assertEqual(len(out_1m), 300)
        self.assertEqual(len(out_5m), 60)
        self.assertEqual(out_5m[0]["time"], base)
        self.assertEqual(out_5m[-1]["time"], base + 59 * 300)


if __name__ == "__main__":
    unittest.main()
