"""Unit tests for Massive bar normalization (no API required)."""

from __future__ import annotations

import unittest

from app.services.massive_bars import agg_to_candle, aggs_to_candles, bar_epoch_seconds, rest_agg_to_candle


class TestMassiveBars(unittest.TestCase):
    def test_bar_epoch_from_ms(self) -> None:
        # 2024-01-01 12:00:00 UTC in ms, aligned to minute
        ms = 1_704_110_400_000
        self.assertEqual(bar_epoch_seconds(ms), 1_704_110_400)

    def test_agg_to_candle_shape(self) -> None:
        msg = {"ev": "AM", "sym": "AAPL", "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 1200, "s": 1_704_110_400_000}
        candle = agg_to_candle(msg)
        self.assertEqual(candle["time"], 1_704_110_400)
        self.assertEqual(candle["close"], 100.5)
        self.assertEqual(candle["volume"], 1200.0)

    def test_aggs_to_candles_skips_empty_time(self) -> None:
        bars = [{"t": 0, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}, {"t": 1_704_110_400_000, "o": 2, "h": 2, "l": 2, "c": 2, "v": 2}]
        out = aggs_to_candles(bars)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["close"], 2.0)

    def test_rest_agg_alias(self) -> None:
        bar = {"t": 1_704_110_400_000, "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 50}
        self.assertEqual(rest_agg_to_candle(bar)["open"], 10.0)

    def test_crypto_xa_message(self) -> None:
        from app.services.massive_bars import crypto_agg_to_candle

        msg = {
            "ev": "XA",
            "pair": "BTC-USD",
            "o": 68000,
            "h": 68100,
            "l": 67900,
            "c": 68050,
            "v": 12.5,
            "s": 1_704_110_400_000,
        }
        candle = crypto_agg_to_candle(msg)
        self.assertEqual(candle["close"], 68050.0)
        self.assertEqual(candle["time"], 1_704_110_400)


if __name__ == "__main__":
    unittest.main()
