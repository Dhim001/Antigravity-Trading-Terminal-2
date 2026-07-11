"""Tests for multi-timeframe backtest candle resolution."""

import os
import time
import unittest
from unittest.mock import patch

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


class ResolveBacktestLongHorizonTests(unittest.TestCase):
    """90d runs must not hard-cap to short 1m retention when broker can fill."""

    def setUp(self):
        self._prev_retention = app_config.ARCHIVE_RETENTION_1M_DAYS
        app_config.ARCHIVE_RETENTION_1M_DAYS = 14
        import app.services.archive.resolve as resolve_mod

        self._resolve_mod = resolve_mod
        self._prev_resolve_ret = resolve_mod.ARCHIVE_RETENTION_1M_DAYS
        resolve_mod.ARCHIVE_RETENTION_1M_DAYS = 14

    def tearDown(self):
        app_config.ARCHIVE_RETENTION_1M_DAYS = self._prev_retention
        self._resolve_mod.ARCHIVE_RETENTION_1M_DAYS = self._prev_resolve_ret

    def test_90d_5m_uses_broker_native_not_cap(self):
        now = int(time.time())
        local_start = now - 10 * 86400
        live = [_bar(local_start + i * 60, 100.0) for i in range(10 * 24 * 60)]

        class _Feed:
            def get_candles(self, symbol):
                return live

        remote_start = now - 90 * 86400
        remote = [
            _bar(remote_start + i * 300, 100.0 + i * 0.01)
            for i in range(90 * 24 * 12)
        ]

        with patch(
            "app.services.archive.broker_fetch.iter_broker_tf_candle_pages",
            side_effect=lambda *a, **k: iter([remote]),
        ) as mock_fetch:
            candles, meta = resolve_backtest_candles(
                "AAPL", _Feed(), days=90, timeframe="5m"
            )

        mock_fetch.assert_called()
        self.assertTrue(meta.get("broker_filled"))
        self.assertIn("broker native", meta["resolution_note"])
        self.assertEqual(meta["effective_days"], 90)
        self.assertGreaterEqual(meta["replayed_days"], 85)
        self.assertGreater(len(candles), 1000)

    def test_90d_1m_broker_fills_gap(self):
        now = int(time.time())
        local_start = now - 10 * 86400
        live = [_bar(local_start + i * 60, 50.0) for i in range(10 * 24 * 60)]

        class _Feed:
            def get_candles(self, symbol):
                return live

        older = [
            _bar(now - (90 * 86400) + i * 60, 49.0)
            for i in range(80 * 24 * 60)
        ]

        with patch(
            "app.services.archive.broker_fetch.iter_broker_tf_candle_pages",
            side_effect=lambda *a, **k: iter([older]),
        ):
            candles, meta = resolve_backtest_candles(
                "AAPL", _Feed(), days=90, timeframe="1m"
            )

        self.assertTrue(meta.get("broker_filled"))
        self.assertGreaterEqual(meta["replayed_days"], 85)
        self.assertGreater(len(candles), 50_000)

    def test_90d_1h_falls_back_to_1h_archive_when_broker_empty(self):
        now = int(time.time())
        hourly = [_bar(now - (90 * 86400) + i * 3600, 10.0) for i in range(90 * 24)]

        class _Feed:
            def get_candles(self, symbol):
                return []

        with patch(
            "app.services.archive.broker_fetch.iter_broker_tf_candle_pages",
            side_effect=lambda *a, **k: iter([]),
        ), patch(
            "app.services.archive.resolve.resolve_candles_for_range",
        ) as mock_range:
            def _side_effect(symbol, feed, *, days=None, interval="auto", **kwargs):
                if interval == "1h":
                    return hourly, {
                        "from": now - 90 * 86400,
                        "to": now,
                        "interval": "1h",
                        "archive_enabled": True,
                    }
                seed = hourly[-14 * 24 :]
                seed_1m = [_bar(h["time"], h["close"]) for h in seed]
                return seed_1m, {
                    "from": now - 14 * 86400,
                    "to": now,
                    "interval": "1m",
                    "archive_enabled": True,
                }

            mock_range.side_effect = _side_effect
            candles, meta = resolve_backtest_candles(
                "AAPL", _Feed(), days=90, timeframe="1h"
            )

        self.assertIn("1h archive", meta["resolution_note"])
        self.assertEqual(meta["effective_days"], 90)
        self.assertGreaterEqual(meta["replayed_days"], 85)
        self.assertGreaterEqual(len(candles), 2000)


if __name__ == "__main__":
    unittest.main()
