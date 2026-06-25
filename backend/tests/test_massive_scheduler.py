"""LIVE_MASSIVE bot scheduler — TICK price hooks and HT BAR_CLOSE via REST."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.bots.massive_scheduler import (
    _ht_last_1m_bar,
    run_massive_bot_tick,
)


class MassiveSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_inactive_when_not_live_massive(self) -> None:
        bot_manager = MagicMock()
        feed = MagicMock()
        with patch("app.services.bots.massive_scheduler.is_live_massive", return_value=False):
            prices = await run_massive_bot_tick(
                bot_manager, feed, MagicMock(), MagicMock(), last_prices={"BTCUSDT": 100.0},
            )
        self.assertEqual(prices, {"BTCUSDT": 100.0})
        bot_manager.process_price_tick.assert_not_called()

    @patch("app.services.bots.massive_scheduler.ALLOW_LIVE_BOTS", True)
    @patch("app.services.bots.massive_scheduler.is_live_massive", return_value=True)
    @patch("app.services.bots.massive_scheduler.run_paper_oms_tick", new_callable=AsyncMock)
    async def test_tick_bot_fires_on_price_change(self, _oms, _live) -> None:
        bot_manager = MagicMock()
        bot_manager.active_bots = {
            "b1": {
                "symbol": "BTCUSDT",
                "status": "RUNNING",
                "execution_mode": "TICK",
            },
        }
        bot_manager.process_price_tick = AsyncMock()
        bot_manager.process_massive_ht_bar_close = AsyncMock()

        feed = MagicMock()
        feed.symbols = ["BTCUSDT"]
        feed.get_market_data.return_value = {"price": 101.0}

        prices = await run_massive_bot_tick(
            bot_manager, feed, MagicMock(), MagicMock(), last_prices={"BTCUSDT": 100.0},
        )
        self.assertEqual(prices["BTCUSDT"], 101.0)
        bot_manager.process_price_tick.assert_awaited_once()
        bot_manager.process_massive_ht_bar_close.assert_not_called()

    @patch("app.services.bots.massive_scheduler.ALLOW_LIVE_BOTS", True)
    @patch("app.services.bots.massive_scheduler.is_live_massive", return_value=True)
    @patch("app.services.bots.massive_scheduler.run_paper_oms_tick", new_callable=AsyncMock)
    async def test_tick_bot_skips_unchanged_price(self, _oms, _live) -> None:
        bot_manager = MagicMock()
        bot_manager.active_bots = {
            "b1": {
                "symbol": "BTCUSDT",
                "status": "RUNNING",
                "execution_mode": "TICK",
            },
        }
        bot_manager.process_price_tick = AsyncMock()
        bot_manager.process_massive_ht_bar_close = AsyncMock()

        feed = MagicMock()
        feed.symbols = ["BTCUSDT"]
        feed.get_market_data.return_value = {"price": 100.0}

        await run_massive_bot_tick(
            bot_manager, feed, MagicMock(), MagicMock(), last_prices={"BTCUSDT": 100.0},
        )
        bot_manager.process_price_tick.assert_not_called()

    @patch("app.services.bots.massive_scheduler.ALLOW_LIVE_BOTS", True)
    @patch("app.services.bots.massive_scheduler.is_live_massive", return_value=True)
    @patch("app.services.bots.massive_scheduler.run_paper_oms_tick", new_callable=AsyncMock)
    async def test_ht_bar_close_only_for_non_tick_ht_bots(self, _oms, _live) -> None:
        bot_manager = MagicMock()
        bot_manager.active_bots = {
            "ht": {
                "symbol": "AAPL",
                "status": "RUNNING",
                "execution_mode": "BAR_CLOSE",
                "timeframe": "1h",
            },
            "tick": {
                "symbol": "BTCUSDT",
                "status": "RUNNING",
                "execution_mode": "TICK",
            },
        }
        bot_manager.process_price_tick = AsyncMock()
        bot_manager.process_massive_ht_bar_close = AsyncMock()

        feed = MagicMock()
        feed.symbols = ["AAPL", "BTCUSDT"]
        feed.get_market_data.return_value = {"price": 150.0}
        feed.get_candles.return_value = [{"time": 1_700_000_000, "close": 150.0}]

        _ht_last_1m_bar.clear()
        await run_massive_bot_tick(bot_manager, feed, MagicMock(), MagicMock())
        bot_manager.process_massive_ht_bar_close.assert_awaited_once_with("AAPL", feed, {"1h"})

        bot_manager.process_massive_ht_bar_close.reset_mock()
        await run_massive_bot_tick(bot_manager, feed, MagicMock(), MagicMock())
        bot_manager.process_massive_ht_bar_close.assert_not_called()

    @patch("app.services.bots.massive_scheduler.ALLOW_LIVE_BOTS", True)
    @patch("app.services.bots.massive_scheduler.is_live_massive", return_value=True)
    @patch("app.services.bots.massive_scheduler.run_paper_oms_tick", new_callable=AsyncMock)
    async def test_ht_eval_runs_on_new_1m_bar(self, _oms, _live) -> None:
        bot_manager = MagicMock()
        bot_manager.active_bots = {
            "ht": {
                "symbol": "AAPL",
                "status": "RUNNING",
                "execution_mode": "BAR_CLOSE",
                "timeframe": "1h",
            },
        }
        bot_manager.process_massive_ht_bar_close = AsyncMock()

        feed = MagicMock()
        feed.symbols = ["AAPL"]
        feed.get_candles.return_value = [{"time": 1_700_000_000, "close": 150.0}]

        _ht_last_1m_bar.clear()
        await run_massive_bot_tick(bot_manager, feed, MagicMock(), MagicMock())
        self.assertEqual(bot_manager.process_massive_ht_bar_close.await_count, 1)

        feed.get_candles.return_value = [{"time": 1_700_000_060, "close": 151.0}]
        await run_massive_bot_tick(bot_manager, feed, MagicMock(), MagicMock())
        self.assertEqual(bot_manager.process_massive_ht_bar_close.await_count, 2)


if __name__ == "__main__":
    unittest.main()
