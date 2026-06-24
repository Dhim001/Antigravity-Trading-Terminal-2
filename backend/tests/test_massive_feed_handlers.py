"""Unit tests for Massive feed handlers (no API key required)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.massive_feed import MassiveFeedService, ROLLING_24H_SEC, rolling_24h_stats


def _minimal_feed() -> MassiveFeedService:
    with patch.object(MassiveFeedService, "__init__", lambda self: None):
        feed = MassiveFeedService()
    feed._symbols = {
        "AAPL": {"price": 100.0, "decimals": 2, "asset": "AAPL", "quote": "USD"},
        "BTCUSDT": {"price": 50000.0, "decimals": 2, "asset": "BTC", "quote": "USD"},
    }
    feed._equity_symbols = ["AAPL"]
    feed._crypto_symbols = ["BTCUSDT"]
    feed._pair_to_terminal = {"BTC-USD": "BTCUSDT", "BTC-USD".upper(): "BTCUSDT"}
    feed.candles = {
        "AAPL": [{"time": 1_700_000_000, "open": 99.0, "high": 100.0, "low": 98.0, "close": 99.5, "volume": 10}],
        "BTCUSDT": [],
    }
    feed.order_books = {}
    feed.broadcast_callback = None
    feed.active = False
    feed._stocks_task = None
    feed._crypto_task = None
    feed._seed_task = None
    feed._poll_task = None
    feed._bar_close = MagicMock()
    feed._stocks_connected = False
    feed._crypto_connected = False
    feed._last_error = None
    feed._stocks_subscriptions = 0
    feed._crypto_subscriptions = 0
    feed._reconnect_count = 0
    feed._trades_received = 0
    feed._bars_received = 0
    feed._quotes_received = 0
    feed._poll_updates = 0
    feed._stocks_ws_give_up = False
    feed._crypto_ws_give_up = False
    feed._stocks_mode = "websocket"
    feed._crypto_mode = "websocket"
    feed._real_quotes = set()
    feed._seeded = {"AAPL"}
    return feed


class TestMassiveFeedHandlers(unittest.TestCase):
    def test_massive_status_includes_modes(self) -> None:
        feed = _minimal_feed()
        feed._stocks_connected = True
        feed._quotes_received = 3
        feed._real_quotes = {"AAPL"}
        status = feed.massive_status
        self.assertTrue(status["connected"])
        self.assertEqual(status["stocks_mode"], "websocket")
        self.assertEqual(status["quotes_received"], 3)
        self.assertEqual(status["real_quote_symbols"], 1)

    def test_massive_status_includes_per_market_lag(self) -> None:
        feed = _minimal_feed()
        base = int(__import__("time").time()) - 120
        feed.candles["AAPL"] = [
            {"time": base, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1},
        ]
        feed._seeded.add("AAPL")
        status = feed.massive_status
        self.assertIsNotNone(status.get("stocks_lag_sec"))

    def test_apply_nbbo_sets_real_orderbook(self) -> None:
        feed = _minimal_feed()
        feed._apply_nbbo("AAPL", 99.5, 100, 100.5, 200)
        ob = feed.order_books["AAPL"]
        self.assertIn("AAPL", feed._real_quotes)
        self.assertEqual(ob["bids"][0][0], 99.5)
        self.assertEqual(ob["asks"][0][0], 100.5)

    def test_stock_quote_event_updates_nbbo(self) -> None:
        feed = _minimal_feed()
        with patch("app.services.massive_feed.MASSIVE_QUOTES_ENABLED", True):
            msg = {"ev": "Q", "sym": "AAPL", "bp": 100.0, "ap": 100.2, "bs": 50, "as": 40}
            sym = feed._resolve_terminal_symbol(msg, "stocks")
            self.assertEqual(sym, "AAPL")
            feed._apply_nbbo(sym, msg["bp"], msg["bs"], msg["ap"], msg["as"])
        self.assertEqual(feed.order_books["AAPL"]["bids"][0][0], 100.0)

    def test_crypto_quote_event_resolves_pair(self) -> None:
        feed = _minimal_feed()
        msg = {"ev": "XQ", "pair": "BTC-USD", "bp": 50000, "ap": 50010, "bs": 1, "as": 2}
        sym = feed._resolve_terminal_symbol(msg, "crypto")
        self.assertEqual(sym, "BTCUSDT")
        feed._apply_nbbo(sym, msg["bp"], msg["bs"], msg["ap"], msg["as"])
        self.assertEqual(feed.order_books["BTCUSDT"]["asks"][0][0], 50010)

    def test_activate_poll_fallback(self) -> None:
        feed = _minimal_feed()
        with patch("app.services.massive_feed.inc") as mock_inc:
            feed._activate_poll_fallback("stocks", "stocks: auth_failed")
        self.assertTrue(feed._stocks_ws_give_up)
        self.assertEqual(feed._stocks_mode, "poll")
        self.assertFalse(feed._stocks_connected)
        mock_inc.assert_called()

    def test_subscription_params_includes_quotes(self) -> None:
        feed = _minimal_feed()
        with patch("app.services.massive_feed.MASSIVE_QUOTES_ENABLED", True):
            params = feed._subscription_params("stocks")
        self.assertIn("Q.AAPL", params)
        self.assertIn("AM.AAPL", params)
        with patch("app.services.massive_feed.MASSIVE_QUOTES_ENABLED", True):
            crypto_params = feed._subscription_params("crypto")
        self.assertIn("XQ.BTC-USD", crypto_params)

    def test_live_candle_snapshot_merges_price(self) -> None:
        feed = _minimal_feed()
        feed._symbols["AAPL"]["price"] = 101.25
        snap = feed._live_candle_snapshot("AAPL")
        self.assertEqual(snap["close"], 101.25)
        self.assertEqual(snap["high"], 101.25)

    def test_init_starts_with_empty_candles(self) -> None:
        with patch("app.services.massive_feed.SYMBOLS", {"AAPL": {"price": 1.0, "decimals": 2}}):
            feed = MassiveFeedService()
        self.assertEqual(feed.candles["AAPL"], [])
        self.assertNotIn("AAPL", feed._seeded)

    def test_unseeded_market_data_omits_candle(self) -> None:
        feed = _minimal_feed()
        feed._seeded.discard("AAPL")
        feed.candles["AAPL"] = []
        md = feed.get_market_data("AAPL")
        self.assertEqual(md["candle"], {})
        self.assertEqual(md["change_24h"], 0.0)

    def test_rolling_24h_stats_window(self) -> None:
        base = 1_700_000_000
        candles = [
            {"time": base, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10},
            {"time": base + 3600, "open": 100.0, "high": 105.0, "low": 99.5, "close": 104.0, "volume": 20},
        ]
        change, vol, hi, lo = rolling_24h_stats(candles, 105.0, now=base + 7200)
        self.assertEqual(change, 5.0)
        self.assertEqual(vol, 30.0)
        self.assertEqual(hi, 105.0)
        self.assertEqual(lo, 99.0)

    def test_rolling_24h_excludes_bars_older_than_24h(self) -> None:
        base = 1_700_000_000
        old = base - ROLLING_24H_SEC - 3600
        candles = [
            {"time": old, "open": 50.0, "high": 55.0, "low": 49.0, "close": 52.0, "volume": 1000},
            {"time": base, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10},
        ]
        change, vol, _, _ = rolling_24h_stats(candles, 101.0, now=base + 60)
        self.assertEqual(change, 1.0)
        self.assertEqual(vol, 10.0)


if __name__ == "__main__":
    unittest.main()
