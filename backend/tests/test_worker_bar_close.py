"""Tests for worker bar-close event wiring."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from app.api.handlers import bots  # noqa: F401 — register routes, break import cycle
from app.services.events import channels
from app.services.events.event_bus import LocalEventBus


def _register_worker_handlers(*args, **kwargs):
    from app.services.bots.runtime import register_worker_handlers

    return register_worker_handlers(*args, **kwargs)


class TestWorkerBarClose(unittest.IsolatedAsyncioTestCase):
    async def test_bar_close_invokes_process_market_tick(self):
        bot_manager = MagicMock()
        bot_manager.process_market_tick = AsyncMock()
        bus = LocalEventBus()
        _register_worker_handlers(bot_manager, bus, feed=None, oms=None, chart_analyst=None)
        await bus.start()

        payload = {
            "symbol": "BTCUSDT",
            "candles": [
                {"time": 1_700_000_000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10},
            ],
        }
        await bus.publish(channels.BAR_CLOSE, payload)

        bot_manager.process_market_tick.assert_awaited_once()
        args, kwargs = bot_manager.process_market_tick.await_args
        self.assertEqual(args[0], "BTCUSDT")
        self.assertIn("ohlcv_1m", kwargs)
        self.assertEqual(kwargs["ohlcv_1m"], payload["candles"])

    async def test_bar_close_syncs_feed_bar_when_present(self):
        bot_manager = MagicMock()
        bot_manager.process_market_tick = AsyncMock()
        feed = MagicMock()
        feed.sync_bar = MagicMock()
        bus = LocalEventBus()
        _register_worker_handlers(bot_manager, bus, feed=feed, oms=None)
        await bus.start()

        bar = {"time": 99, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1}
        await bus.publish(channels.BAR_CLOSE, {"symbol": "ETHUSDT", "bar": bar, "candles": [bar]})

        feed.sync_bar.assert_called_once_with("ETHUSDT", [bar])
        bot_manager.process_market_tick.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
