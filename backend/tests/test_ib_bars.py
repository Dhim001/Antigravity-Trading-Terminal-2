"""Unit tests for IB bar normalization (no Gateway required)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.ib_bars import bar_epoch_seconds, bar_to_candle, bars_to_candles


class TestIbBars(unittest.TestCase):
    def test_bar_epoch_from_datetime(self):
        dt = datetime(2026, 1, 15, 14, 31, tzinfo=timezone.utc)
        bar = SimpleNamespace(date=dt, open=1, high=2, low=0.5, close=1.5, volume=100)
        self.assertEqual(bar_epoch_seconds(bar) // 60, int(dt.timestamp()) // 60)

    def test_bar_to_candle_shape(self):
        epoch = 1_700_000_100
        bar = SimpleNamespace(
            date=datetime.fromtimestamp(epoch, tz=timezone.utc),
            open=100.0,
            high=101.0,
            low=99.5,
            close=100.5,
            volume=1234.5,
        )
        candle = bar_to_candle(bar)
        self.assertEqual(candle["time"], (epoch // 60) * 60)
        self.assertEqual(candle["close"], 100.5)
        self.assertEqual(candle["volume"], 1234.5)

    def test_bars_to_candles_skips_empty_time(self):
        bars = [
            SimpleNamespace(date=None, open=1, high=1, low=1, close=1, volume=1),
            SimpleNamespace(
                date=datetime.fromtimestamp(1_700_000_000, tz=timezone.utc),
                open=1,
                high=2,
                low=1,
                close=1.5,
                volume=10,
            ),
        ]
        out = bars_to_candles(bars)
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
