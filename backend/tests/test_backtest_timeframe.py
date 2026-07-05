"""Tests for multi-timeframe backtest candle resolution."""

import os
import time
import unittest

os.environ.setdefault("ARCHIVE_ENABLED", "false")

import app.config as app_config  # noqa: E402

app_config.ARCHIVE_ENABLED = False

from app.services.archive.resolve import resolve_backtest_candles  # noqa: E402


def _recent_base(align_secs: int = 60) -> int:
    now = int(time.time())
    return (now // align_secs) * align_secs - 7 * 86400


def _bar(time_sec: int, close: float) -> dict:
    return {
        "time": time_sec,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1.0,
    }


class ResolveBacktestTimeframeTests(unittest.TestCase):
    def test_1m_default_unchanged(self):
        base = _recent_base(60)
        live = [_bar(base + i * 60, 100.0 + i) for i in range(300)]

        class _Feed:
            def get_candles(self, symbol):
                return live

        candles, meta = resolve_backtest_candles("AAPL", _Feed(), days=7, timeframe="1m")
        self.assertEqual(meta["timeframe"], "1m")
        self.assertIn("1m bars", meta["resolution_note"])
        self.assertGreaterEqual(len(candles), 200)

    def test_5m_resamples_from_1m(self):
        base = _recent_base(300)
        live = [_bar(base + i * 60, 100.0 + i) for i in range(600)]

        class _Feed:
            def get_candles(self, symbol):
                return live

        candles, meta = resolve_backtest_candles("AAPL", _Feed(), days=7, timeframe="5m")
        self.assertEqual(meta["timeframe"], "5m")
        self.assertIn("resampled to 5m", meta["resolution_note"])
        self.assertGreaterEqual(meta["bars_1m"], 500)
        self.assertGreaterEqual(len(candles), 100)
        self.assertLessEqual(len(candles), 125)
        self.assertIn("replayed_days", meta)
        self.assertGreater(meta["replayed_days"], 0)

    def test_5m_not_truncated_to_bot_min_candles(self):
        """Resampled backtests must not cap to ~300 bars (BOT_MIN_CANDLES + 100)."""
        base = _recent_base(60)
        live = [_bar(base + i * 60, 100.0) for i in range(2500)]

        class _Feed:
            def get_candles(self, symbol):
                return live

        candles, meta = resolve_backtest_candles("AAPL", _Feed(), days=7, timeframe="5m")
        self.assertGreater(len(candles), 300)
        self.assertEqual(meta["days_requested"], 7)
        self.assertLess(meta["replayed_days"], 7)
        self.assertIn("Replayed", meta.get("range_note", ""))

    def test_invalid_timeframe_raises(self):
        class _Feed:
            def get_candles(self, symbol):
                return []

        with self.assertRaises(ValueError):
            resolve_backtest_candles("AAPL", _Feed(), timeframe="2m")

    def test_1h_alias_normalizes(self):
        base = _recent_base(3600)
        live = [_bar(base + i * 60, 50.0 + i * 0.1) for i in range(3600)]

        class _Feed:
            def get_candles(self, symbol):
                return live

        candles, meta = resolve_backtest_candles("AAPL", _Feed(), days=7, timeframe="1H")
        self.assertEqual(meta["timeframe"], "1h")
        self.assertGreater(len(candles), 50)
        self.assertLess(len(candles), len(live))


if __name__ == "__main__":
    unittest.main()
