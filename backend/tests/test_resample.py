"""Tests for market timeframe registry and OHLCV resampling."""

import unittest

from app.services.market.resample import resample_candles, resample_candles_for_timeframe
from app.services.market.timeframes import (
    is_valid_timeframe,
    normalize_timeframe,
    timeframe_to_secs,
)


def _bar(time_sec: int, o: float, h: float, l: float, c: float, vol: float = 1.0) -> dict:
    return {
        "time": time_sec,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": vol,
    }


class TimeframeRegistryTests(unittest.TestCase):
    def test_normalize_aliases(self):
        self.assertEqual(normalize_timeframe("1H"), "1h")
        self.assertEqual(normalize_timeframe("4H"), "4h")
        self.assertEqual(normalize_timeframe("1D"), "1d")
        self.assertEqual(normalize_timeframe("1m"), "1m")

    def test_normalize_tick(self):
        self.assertEqual(normalize_timeframe("tick"), "tick")
        self.assertEqual(normalize_timeframe("TICK"), "tick")

    def test_invalid_timeframe_raises(self):
        with self.assertRaises(ValueError):
            normalize_timeframe("2m")
        self.assertFalse(is_valid_timeframe("bogus"))

    def test_timeframe_to_secs(self):
        self.assertEqual(timeframe_to_secs("5m"), 300)
        self.assertEqual(timeframe_to_secs("1H"), 3600)

    def test_tick_has_no_bar_interval(self):
        with self.assertRaises(ValueError):
            timeframe_to_secs("tick")


class ResampleCandlesTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(resample_candles([], 300), [])

    def test_single_bar(self):
        raw = [_bar(300, 10, 11, 9, 10.5, 2)]
        out = resample_candles(raw, 300)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["time"], 300)
        self.assertEqual(out[0]["open"], 10)
        self.assertEqual(out[0]["close"], 10.5)
        self.assertEqual(out[0]["volume"], 2)

    def test_five_one_minute_bars_into_one_five_minute_bar(self):
        base = (1_700_000_000 // 300) * 300
        raw = [
            _bar(base + 0, 100, 101, 99, 100.5, 1),
            _bar(base + 60, 100.5, 102, 100, 101, 2),
            _bar(base + 120, 101, 103, 100.5, 102, 3),
            _bar(base + 180, 102, 104, 101, 103, 4),
            _bar(base + 240, 103, 105, 102.5, 104, 5),
        ]
        out = resample_candles(raw, 300)
        self.assertEqual(len(out), 1)
        bar = out[0]
        self.assertEqual(bar["time"], base)
        self.assertEqual(bar["open"], 100)
        self.assertEqual(bar["high"], 105)
        self.assertEqual(bar["low"], 99)
        self.assertEqual(bar["close"], 104)
        self.assertEqual(bar["volume"], 15)

    def test_partial_trailing_bucket_included(self):
        base = (1_700_000_000 // 300) * 300
        raw = [
            _bar(base + 0, 10, 11, 9, 10, 0),
            _bar(base + 60, 10, 11, 9.5, 10.5, 0),
            _bar(base + 120, 10.5, 11.5, 10, 11, 0),
            _bar(base + 180, 11, 12, 10.5, 11.5, 0),
        ]
        out = resample_candles(raw, 300)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["close"], 11.5)
        self.assertEqual(out[0]["volume"], 0)

    def test_multiple_buckets_sorted(self):
        base = 1_700_000_000
        raw = [
            _bar(base + 0, 1, 2, 0.5, 1.5),
            _bar(base + 300, 2, 3, 1.5, 2.5),
        ]
        out = resample_candles(raw, 300)
        self.assertEqual(len(out), 2)
        self.assertLess(out[0]["time"], out[1]["time"])

    def test_millisecond_timestamps(self):
        raw = [_bar(1_700_000_000_000, 50, 51, 49, 50.5)]
        out = resample_candles(raw, 60)
        self.assertEqual(out[0]["time"], (1_700_000_000 // 60) * 60)

    def test_invalid_interval_raises(self):
        with self.assertRaises(ValueError):
            resample_candles([], 0)

    def test_resample_for_timeframe_wrapper(self):
        base = (1_700_000_000 // 300) * 300
        raw = [_bar(base + i * 60, 10 + i, 11 + i, 9 + i, 10 + i, 1) for i in range(5)]
        out = resample_candles_for_timeframe(raw, "5m")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["volume"], 5)

    def test_golden_five_minute_aggregation(self):
        """Hand-verified bucket — matches ChartWidget bucketCandles semantics."""
        base = (1_710_000_000 // 300) * 300
        raw = [
            _bar(base + 0, 200, 201, 199, 200.2, 10),
            _bar(base + 60, 200.2, 202, 200, 201.0, 20),
            _bar(base + 120, 201.0, 203, 200.5, 202.0, 30),
            _bar(base + 180, 202.0, 204, 201.5, 203.0, 40),
            _bar(base + 240, 203.0, 205, 202.0, 204.0, 50),
            _bar(base + 300, 204.0, 206, 203.0, 205.0, 5),
        ]
        out = resample_candles(raw, 300)
        self.assertEqual(len(out), 2)
        first = out[0]
        self.assertEqual(first["open"], 200)
        self.assertEqual(first["high"], 205)
        self.assertEqual(first["low"], 199)
        self.assertEqual(first["close"], 204.0)
        self.assertEqual(first["volume"], 150)
        second = out[1]
        self.assertEqual(second["open"], 204.0)
        self.assertEqual(second["close"], 205.0)
        self.assertEqual(second["volume"], 5)


if __name__ == "__main__":
    unittest.main()
