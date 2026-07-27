"""Alpaca forming/closed candle rules — official bars + no fake quote wicks."""

from __future__ import annotations

import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.services.alpaca_feed import AlpacaFeedService


def _feed_with_symbol(symbol: str = "BTCUSDT", price: float = 100.0) -> AlpacaFeedService:
    feed = AlpacaFeedService.__new__(AlpacaFeedService)
    feed._symbols = {
        symbol: {
            "price": price,
            "decimals": 2,
            "asset_class": "crypto",
            "bid": price - 1.0,
            "ask": price + 1.0,
        }
    }
    bucket = int(time.time() // 60) * 60
    feed.candles = {
        symbol: [
            {
                "time": bucket,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0,
            }
        ]
    }
    feed._bar_close = MagicMock()
    feed._bar_close.notify = MagicMock()
    feed._pending_updates = set()
    feed._last_quote_apply_ts = {}
    feed._crypto_last_trade_event_ts = {}
    feed._sealed_bar_ts = {}
    feed._status = {"crypto": {}, "equity": {}, "options": {}}
    return feed


class AlpacaCandleFormationTests(unittest.TestCase):
    def test_new_minute_opens_flat_despite_wide_quote_spread(self) -> None:
        feed = _feed_with_symbol("ETHUSDT", 2000.0)
        prev = int(time.time() // 60) * 60 - 60
        feed.candles["ETHUSDT"] = [
            {"time": prev, "open": 1990.0, "high": 1995.0, "low": 1988.0, "close": 1992.0, "volume": 1.0}
        ]
        feed._patch_forming_candle("ETHUSDT", 2000.0, from_quote=True)
        bar = feed.candles["ETHUSDT"][-1]
        self.assertEqual(bar["open"], bar["high"])
        self.assertEqual(bar["high"], bar["low"])
        self.assertEqual(bar["low"], bar["close"])
        self.assertEqual(bar["close"], 2000.0)
        feed._bar_close.notify.assert_called_once_with("ETHUSDT")

    def test_quote_updates_last_without_bid_ask_spread_wick(self) -> None:
        feed = _feed_with_symbol("BTCUSDT", 100.0)
        feed._symbols["BTCUSDT"]["bid"] = 90.0
        feed._symbols["BTCUSDT"]["ask"] = 110.0
        feed._patch_forming_candle("BTCUSDT", 101.0, from_quote=True)
        bar = feed.candles["BTCUSDT"][-1]
        self.assertEqual(bar["open"], 100.0)
        self.assertEqual(bar["high"], 101.0)
        self.assertEqual(bar["low"], 100.0)
        self.assertEqual(bar["close"], 101.0)
        self.assertNotEqual(bar["low"], 90.0)
        self.assertNotEqual(bar["high"], 110.0)

    def test_trade_expands_high_low_and_volume(self) -> None:
        feed = _feed_with_symbol("BTCUSDT", 100.0)
        feed._patch_forming_candle("BTCUSDT", 102.5, from_quote=False, volume=1.5)
        feed._patch_forming_candle("BTCUSDT", 99.0, from_quote=False, volume=0.5)
        bar = feed.candles["BTCUSDT"][-1]
        self.assertEqual(bar["open"], 100.0)
        self.assertEqual(bar["high"], 102.5)
        self.assertEqual(bar["low"], 99.0)
        self.assertEqual(bar["close"], 99.0)
        self.assertAlmostEqual(bar["volume"], 2.0)

    def test_official_bar_replaces_provisional_same_minute(self) -> None:
        feed = _feed_with_symbol("AAPL", 180.0)
        bucket = int(time.time() // 60) * 60
        feed.candles["AAPL"] = [
            {"time": bucket, "open": 180.0, "high": 181.0, "low": 179.5, "close": 180.5, "volume": 0.0}
        ]
        feed._symbols["AAPL"] = {
            "price": 180.5,
            "decimals": 2,
            "asset_class": "equity",
        }
        ts = datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        feed._apply_bar(
            "AAPL",
            {"o": 180.1, "h": 182.0, "l": 179.0, "c": 181.2, "v": 1200, "t": ts},
            updated=False,
        )
        bar = feed.candles["AAPL"][-1]
        self.assertEqual(bar["open"], 180.1)
        self.assertEqual(bar["high"], 182.0)
        self.assertEqual(bar["low"], 179.0)
        self.assertEqual(bar["close"], 181.2)
        self.assertEqual(bar["volume"], 1200)
        self.assertEqual(feed._sealed_bar_ts["AAPL"], bucket)
        feed._bar_close.notify.assert_not_called()

    def test_sealed_bar_ignores_provisional_quote_patch(self) -> None:
        feed = _feed_with_symbol("AAPL", 180.0)
        bucket = int(time.time() // 60) * 60
        feed.candles["AAPL"] = [
            {"time": bucket, "open": 180.0, "high": 181.0, "low": 179.0, "close": 180.5, "volume": 50}
        ]
        feed._symbols["AAPL"] = {"price": 180.5, "decimals": 2, "asset_class": "equity"}
        feed._sealed_bar_ts["AAPL"] = bucket
        feed._patch_forming_candle("AAPL", 185.0, from_quote=True)
        bar = feed.candles["AAPL"][-1]
        self.assertEqual(bar["close"], 180.5)
        self.assertEqual(bar["high"], 181.0)

    def test_updated_bar_revises_prior_minute_without_bar_close(self) -> None:
        feed = _feed_with_symbol("AAPL", 180.0)
        now = int(time.time() // 60) * 60
        prev = now - 60
        feed.candles["AAPL"] = [
            {"time": prev, "open": 180.0, "high": 181.0, "low": 179.0, "close": 180.5, "volume": 100},
            {"time": now, "open": 180.5, "high": 180.5, "low": 180.5, "close": 180.5, "volume": 0},
        ]
        feed._symbols["AAPL"] = {"price": 180.5, "decimals": 2, "asset_class": "equity"}
        ts = datetime.fromtimestamp(prev, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        feed._apply_bar(
            "AAPL",
            {"o": 180.0, "h": 181.5, "l": 178.5, "c": 181.0, "v": 150, "t": ts},
            updated=True,
        )
        self.assertEqual(feed.candles["AAPL"][0]["high"], 181.5)
        self.assertEqual(feed.candles["AAPL"][0]["volume"], 150)
        self.assertEqual(feed.candles["AAPL"][-1]["time"], now)
        feed._bar_close.notify.assert_not_called()

    def test_live_snapshot_ignores_bid_ask_spread(self) -> None:
        feed = _feed_with_symbol("BTCUSDT", 100.0)
        feed._symbols["BTCUSDT"]["price"] = 100.5
        feed._symbols["BTCUSDT"]["bid"] = 90.0
        feed._symbols["BTCUSDT"]["ask"] = 110.0
        snap = feed._live_candle_snapshot("BTCUSDT")
        self.assertEqual(snap["close"], 100.5)
        self.assertEqual(snap["high"], 100.5)
        self.assertEqual(snap["low"], 100.0)


if __name__ == "__main__":
    unittest.main()
