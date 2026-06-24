"""Unit tests for IB feed error handling (no Gateway required)."""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from app.services.ib_feed import IbFeedService


class TestIbFeedHandlers(unittest.TestCase):
    def setUp(self) -> None:
        with patch.object(IbFeedService, "__init__", lambda self: None):
            self.feed = IbFeedService()
        self.feed._pacing_paused_until = 0.0
        self.feed._market_data_type = 1
        self.feed._market_data_delayed = False
        self.feed._ib = MagicMock()
        self.feed._last_error = None
        self.feed._reconnect_count = 0

    def test_pacing_error_sets_pause(self) -> None:
        with patch("app.services.ib_feed.IB_PACING_PAUSE_SEC", 120.0):
            with patch("app.services.ib_feed.inc") as mock_inc:
                self.feed._on_ib_error(0, 162, "pacing", None)
        self.assertGreater(self.feed._pacing_paused_until, time.time())
        mock_inc.assert_called()

    def test_delayed_fallback_on_subscription_error(self) -> None:
        with patch("app.services.ib_feed.IB_AUTO_DELAYED_FALLBACK", True):
            self.feed._apply_delayed_fallback = MagicMock()
            self.feed._on_ib_error(0, 10167, "no subscription", None)
        self.feed._apply_delayed_fallback.assert_called_once()

    def test_ib_status_includes_delayed_flag(self) -> None:
        self.feed._connected = True
        self.feed._streams_active = 3
        self.feed._market_data_type = 3
        self.feed._market_data_delayed = True
        self.feed._reconnect_count = 0
        status = self.feed.ib_status
        self.assertTrue(status["market_data_delayed"])
        self.assertEqual(status["market_data_type"], 3)

    def test_live_candle_snapshot_merges_l1_price(self) -> None:
        with patch.object(IbFeedService, "__init__", lambda self: None):
            feed = IbFeedService()
        feed._symbols = {"AAPL": {"price": 100.0, "decimals": 2, "asset": "AAPL", "quote": "USD"}}
        feed.candles = {
            "AAPL": [{"time": 1_700_000_000, "open": 99.0, "high": 100.0, "low": 98.0, "close": 99.5, "volume": 10}],
        }
        feed._symbols["AAPL"]["price"] = 101.25
        snap = feed._live_candle_snapshot("AAPL")
        self.assertEqual(snap["close"], 101.25)
        self.assertEqual(snap["high"], 101.25)
        self.assertEqual(snap["low"], 98.0)


if __name__ == "__main__":
    unittest.main()
