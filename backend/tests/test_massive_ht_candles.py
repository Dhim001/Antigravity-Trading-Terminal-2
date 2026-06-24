"""Tests for Massive native higher-timeframe REST fetch."""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from app.services.massive_bars import (
    aggs_to_candles_native,
    rest_agg_to_candle_native,
    timeframe_to_massive_range,
)
from app.services.massive_feed import MassiveFeedService


class TestMassiveHtBars(unittest.TestCase):
    def test_timeframe_to_massive_range(self) -> None:
        self.assertEqual(timeframe_to_massive_range("5m"), (5, "minute"))
        self.assertEqual(timeframe_to_massive_range("1H"), (1, "hour"))
        self.assertEqual(timeframe_to_massive_range("4h"), (4, "hour"))
        self.assertEqual(timeframe_to_massive_range("1d"), (1, "day"))

    def test_rest_agg_native_preserves_hour_start(self) -> None:
        # 2024-01-01 10:00:00 UTC
        start_ms = 1_704_105_600_000
        candle = rest_agg_to_candle_native({"s": start_ms, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10})
        self.assertEqual(candle["time"], start_ms // 1000)

    def test_aggs_to_candles_native(self) -> None:
        bars = [{"s": 1_704_105_600_000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}]
        out = aggs_to_candles_native(bars)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["close"], 1.5)


class TestMassiveFeedHtFetch(unittest.TestCase):
    def _feed(self) -> MassiveFeedService:
        with patch.object(MassiveFeedService, "__init__", lambda self: None):
            feed = MassiveFeedService()
        feed._symbols = {"AAPL": {"price": 100, "decimals": 2, "asset": "AAPL", "quote": "USD"}}
        feed._ht_cache = {}
        feed.candles = {"AAPL": []}
        return feed

    @patch("app.services.massive_feed.MASSIVE_API_KEY", "test-key")
    @patch("app.services.massive_feed.httpx.Client")
    def test_fetch_ht_candles_caches(self, mock_client_cls) -> None:
        feed = self._feed()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"s": 1_704_105_600_000, "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 1000},
            ],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp

        first = feed.fetch_ht_candles("AAPL", "1h", limit=100)
        second = feed.fetch_ht_candles("AAPL", "1h", limit=100)

        self.assertEqual(len(first), 1)
        self.assertEqual(first, second)
        mock_client_cls.return_value.__enter__.return_value.get.assert_called_once()

    @patch("app.services.massive_feed.MASSIVE_API_KEY", "test-key")
    @patch("app.services.massive_feed.httpx.Client")
    def test_fetch_ht_chart_warm_serves_deeper_analysis(self, mock_client_cls) -> None:
        """Chart fetch stores analysis depth so bots reuse cache without a second REST call."""
        feed = self._feed()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"s": 1_704_105_600_000 + i * 3_600_000, "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 1000}
                for i in range(1500)
            ],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp

        chart = feed.fetch_ht_candles("AAPL", "1h", limit=600, purpose="chart")
        deep = feed.fetch_ht_candles("AAPL", "1h", limit=1500, purpose="analysis")

        self.assertEqual(len(chart), 600)
        self.assertEqual(len(deep), 1500)
        mock_client_cls.return_value.__enter__.return_value.get.assert_called_once()

    @patch("app.services.massive_feed.MASSIVE_API_KEY", "test-key")
    def test_fetch_ht_1m_delegates_to_memory(self) -> None:
        feed = self._feed()
        feed.candles["AAPL"] = [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}]
        out = feed.fetch_ht_candles("AAPL", "1m", limit=10)
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
