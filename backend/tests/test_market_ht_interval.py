"""Market handler HT interval routing for LIVE_MASSIVE."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.context import RequestContext
from app.api.handlers import market as market_handlers


class TestMarketHtInterval(unittest.TestCase):
    def _ctx(self, message: dict) -> RequestContext:
        feed = MagicMock()
        feed.get_candles.return_value = [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}]
        feed.fetch_ht_candles.return_value = [{"time": 3600, "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 10}]
        oms = SimpleNamespace(feed=feed)
        manager = MagicMock()
        manager.set_client_symbol = MagicMock()
        return RequestContext(
            websocket=MagicMock(),
            manager=manager,
            oms=oms,
            bot_manager=MagicMock(),
            backtester=None,
            chart_analyst=None,
            message=message,
            action="subscribe_symbol",
        )

    @patch.object(market_handlers, "TERMINAL_MODE", "LIVE_MASSIVE")
    @patch.object(market_handlers, "send_history_update", new_callable=AsyncMock)
    @patch.object(market_handlers, "_send_orderbook_snapshot", new_callable=AsyncMock)
    def test_ht_interval_uses_fetch_ht(self, _ob, send_hist) -> None:
        ctx = self._ctx({"symbol": "AAPL", "interval": "1h", "limit": "600"})

        import asyncio
        asyncio.run(market_handlers.subscribe_symbol(ctx))

        ctx.oms.feed.fetch_ht_candles.assert_called_once_with("AAPL", "1h", limit=600)
        send_hist.assert_awaited_once()
        args = send_hist.await_args
        self.assertEqual(args[0][1]["AAPL"][0]["time"], 3600)
        self.assertEqual(args[1]["meta"]["interval"], "1h")

    @patch.object(market_handlers, "TERMINAL_MODE", "SIMULATED")
    @patch.object(market_handlers, "send_history_update", new_callable=AsyncMock)
    @patch.object(market_handlers, "_send_orderbook_snapshot", new_callable=AsyncMock)
    def test_sim_ignores_ht_interval(self, _ob, send_hist) -> None:
        ctx = self._ctx({"symbol": "AAPL", "interval": "1h", "limit": "600"})

        import asyncio
        asyncio.run(market_handlers.subscribe_symbol(ctx))

        ctx.oms.feed.fetch_ht_candles.assert_not_called()
        ctx.oms.feed.get_candles.assert_called_once_with("AAPL")


if __name__ == "__main__":
    unittest.main()
