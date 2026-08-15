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
    feed._queue_market_broadcast = MagicMock()
    feed.order_books = {}
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

    def test_correction_updates_current_forming_minute_close(self) -> None:
        feed = _feed_with_symbol("AAPL", 180.0)
        bucket = int(time.time() // 60) * 60
        feed.candles["AAPL"] = [
            {"time": bucket, "open": 180.0, "high": 181.0, "low": 179.0, "close": 180.5, "volume": 50}
        ]
        feed._symbols["AAPL"] = {"price": 180.5, "decimals": 2, "asset_class": "equity"}
        ts = datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        feed._apply_correction("AAPL", {"t": ts, "p": 182.0, "s": 10, "i": "1", "ci": "2"})
        bar = feed.candles["AAPL"][-1]
        self.assertEqual(bar["close"], 182.0)
        self.assertEqual(bar["high"], 182.0)
        # Volume is intentionally NOT revised (no per-trade ledger).
        self.assertEqual(bar["volume"], 50)

    def test_correction_for_past_sealed_minute_is_ignored(self) -> None:
        feed = _feed_with_symbol("AAPL", 180.0)
        now = int(time.time() // 60) * 60
        prev = now - 60
        feed.candles["AAPL"] = [
            {"time": prev, "open": 180.0, "high": 181.0, "low": 179.0, "close": 180.5, "volume": 100},
            {"time": now, "open": 180.5, "high": 180.5, "low": 180.5, "close": 180.5, "volume": 0},
        ]
        feed._symbols["AAPL"] = {"price": 180.5, "decimals": 2, "asset_class": "equity"}
        feed._sealed_bar_ts["AAPL"] = prev
        ts = datetime.fromtimestamp(prev, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        feed._apply_correction("AAPL", {"t": ts, "p": 200.0, "s": 10})
        # Past sealed minute untouched — updatedBars is the reconciliation path.
        self.assertEqual(feed.candles["AAPL"][0]["close"], 180.5)
        self.assertEqual(feed.candles["AAPL"][-1]["close"], 180.5)

    def test_correction_on_sealed_current_minute_is_ignored(self) -> None:
        feed = _feed_with_symbol("AAPL", 180.0)
        bucket = int(time.time() // 60) * 60
        feed.candles["AAPL"] = [
            {"time": bucket, "open": 180.0, "high": 181.0, "low": 179.0, "close": 180.5, "volume": 50}
        ]
        feed._symbols["AAPL"] = {"price": 180.5, "decimals": 2, "asset_class": "equity"}
        feed._sealed_bar_ts["AAPL"] = bucket
        ts = datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        feed._apply_correction("AAPL", {"t": ts, "p": 200.0, "s": 10})
        self.assertEqual(feed.candles["AAPL"][-1]["close"], 180.5)

    def test_cancel_error_does_not_mutate_candle(self) -> None:
        feed = _feed_with_symbol("AAPL", 180.0)
        bucket = int(time.time() // 60) * 60
        feed.candles["AAPL"] = [
            {"time": bucket, "open": 180.0, "high": 181.0, "low": 179.0, "close": 180.5, "volume": 50}
        ]
        feed._symbols["AAPL"] = {"price": 180.5, "decimals": 2, "asset_class": "equity"}
        before = dict(feed.candles["AAPL"][-1])
        # Should not raise and should not mutate the forming candle.
        feed._apply_cancel_error("AAPL", {"i": "trade-1", "t": "2024-01-01T00:00:00Z"})
        self.assertEqual(feed.candles["AAPL"][-1], before)

    def test_correction_bad_timestamp_is_ignored(self) -> None:
        feed = _feed_with_symbol("AAPL", 180.0)
        bucket = int(time.time() // 60) * 60
        feed.candles["AAPL"] = [
            {"time": bucket, "open": 180.0, "high": 181.0, "low": 179.0, "close": 180.5, "volume": 50}
        ]
        feed._symbols["AAPL"] = {"price": 180.5, "decimals": 2, "asset_class": "equity"}
        feed._apply_correction("AAPL", {"t": "not-a-timestamp", "p": 200.0, "s": 10})
        self.assertEqual(feed.candles["AAPL"][-1]["close"], 180.5)

    def test_crypto_trade_spike_ignored_when_book_near_ref(self) -> None:
        feed = _feed_with_symbol("BTCUSDT", 65000.0)
        feed._symbols["BTCUSDT"]["bid"] = 64990.0
        feed._symbols["BTCUSDT"]["ask"] = 65010.0
        before = dict(feed.candles["BTCUSDT"][-1])
        feed._apply_trade("BTCUSDT", 62767.0, 1.0)
        self.assertEqual(feed.candles["BTCUSDT"][-1], before)
        self.assertEqual(feed._symbols["BTCUSDT"]["price"], 65000.0)

    def test_crypto_trade_accepted_when_book_confirms_move(self) -> None:
        feed = _feed_with_symbol("BTCUSDT", 65000.0)
        # Book already repriced with the dump → treat as real move.
        feed._symbols["BTCUSDT"]["bid"] = 62000.0
        feed._symbols["BTCUSDT"]["ask"] = 62100.0
        feed._apply_trade("BTCUSDT", 62050.0, 0.5)
        bar = feed.candles["BTCUSDT"][-1]
        self.assertEqual(bar["close"], 62050.0)
        self.assertEqual(bar["low"], 62050.0)
        self.assertAlmostEqual(bar["volume"], 0.5)

    def test_crypto_official_bar_spike_ignored(self) -> None:
        feed = _feed_with_symbol("BTCUSDT", 65175.0)
        feed._symbols["BTCUSDT"]["bid"] = 65170.0
        feed._symbols["BTCUSDT"]["ask"] = 65180.0
        now = int(time.time() // 60) * 60
        prev = now - 60
        feed.candles["BTCUSDT"] = [
            {"time": prev, "open": 65170.0, "high": 65180.0, "low": 65160.0, "close": 65175.0, "volume": 0.1},
        ]
        ts = datetime.fromtimestamp(now, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        feed._apply_bar(
            "BTCUSDT",
            {
                "o": 62839.41509161913,
                "h": 62911.00305813065,
                "l": 62767.82712510761,
                "c": 62767.82712510761,
                "v": 702.95,
                "t": ts,
            },
        )
        self.assertEqual(len(feed.candles["BTCUSDT"]), 1)
        self.assertEqual(feed.candles["BTCUSDT"][-1]["close"], 65175.0)

    def test_crypto_seed_filter_drops_isolated_spike(self) -> None:
        feed = _feed_with_symbol("BTCUSDT", 65000.0)
        bars = [
            {"time": 1, "open": 65000.0, "high": 65010.0, "low": 64990.0, "close": 65005.0, "volume": 1},
            {"time": 2, "open": 62800.0, "high": 62900.0, "low": 62700.0, "close": 62750.0, "volume": 700},
            {"time": 3, "open": 65000.0, "high": 65020.0, "low": 64980.0, "close": 65010.0, "volume": 1},
        ]
        out = feed._filter_crypto_seed_bars("BTCUSDT", bars)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["close"], 65005.0)
        self.assertEqual(out[1]["close"], 65010.0)

    def test_equity_bars_not_spike_filtered(self) -> None:
        feed = _feed_with_symbol("AAPL", 180.0)
        feed._symbols["AAPL"] = {"price": 180.0, "decimals": 2, "asset_class": "equity"}
        now = int(time.time() // 60) * 60
        ts = datetime.fromtimestamp(now, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        # A 5% equity bar is unusual but must not be crypto-filtered.
        feed._apply_bar(
            "AAPL",
            {"o": 180.0, "h": 190.0, "l": 179.0, "c": 189.0, "v": 1000, "t": ts},
        )
        self.assertEqual(feed.candles["AAPL"][-1]["close"], 189.0)

    def test_synthetic_book_uses_live_bid_ask_as_bbo(self) -> None:
        feed = _feed_with_symbol("BTCUSDT", 100000.0)
        feed._symbols["BTCUSDT"]["bid"] = 99990.0
        feed._symbols["BTCUSDT"]["ask"] = 100010.0
        book = feed._generate_synthetic_book("BTCUSDT", 100000.0)
        self.assertAlmostEqual(book["bids"][0][0], 99990.0)
        self.assertAlmostEqual(book["asks"][0][0], 100010.0)
        self.assertEqual(len(book["bids"]), 10)
        self.assertEqual(len(book["asks"]), 10)

    def test_apply_trade_refreshes_order_book(self) -> None:
        feed = _feed_with_symbol("AAPL", 180.0)
        feed._symbols["AAPL"]["asset_class"] = "equity"
        feed._apply_trade("AAPL", 181.0, 1.0, bid=180.95, ask=181.05)
        book = feed.order_books["AAPL"]
        self.assertAlmostEqual(book["bids"][0][0], 180.95)
        self.assertAlmostEqual(book["asks"][0][0], 181.05)


if __name__ == "__main__":
    unittest.main()
