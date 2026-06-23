"""Distributed Redis bar-close → worker bot evaluation integration smoke."""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.handlers import bots  # noqa: F401 — register routes
from app.services.bots.runtime import register_worker_handlers
from app.services.events import channels
from app.services.events.event_bus import LocalEventBus, RedisEventBus


def _bar(time_sec: int, close: float) -> dict:
    return {
        "time": time_sec,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1.0,
    }


class TestDistributedBarCloseIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_local_bus_invokes_process_market_tick_with_signal_path(self):
        bot_manager = MagicMock()
        bot_manager.process_market_tick = AsyncMock()
        bot_manager.active_bots = {
            "bot-1": {
                "id": "bot-1",
                "symbol": "BTCUSDT",
                "strategy": "MACD_RSI",
                "timeframe": "5m",
                "status": "RUNNING",
                "execution_mode": "BAR_CLOSE",
            }
        }
        bus = LocalEventBus()
        register_worker_handlers(bot_manager, bus, feed=None, oms=None)
        await bus.start()

        candles = [_bar(1_700_000_000 + i * 60, 100 + i) for i in range(20)]
        await bus.publish(
            channels.BAR_CLOSE,
            {"symbol": "BTCUSDT", "candles": candles, "bar": candles[-2]},
        )

        bot_manager.process_market_tick.assert_awaited_once()
        args, kwargs = bot_manager.process_market_tick.await_args
        self.assertEqual(args[0], "BTCUSDT")
        self.assertIn("ohlcv_1m", kwargs)

    @unittest.skipUnless(os.environ.get("REDIS_URL"), "REDIS_URL required for Redis integration")
    async def test_redis_bus_forwards_bar_close_to_worker_handler(self):
        url = os.environ["REDIS_URL"]
        bot_manager = MagicMock()
        bot_manager.process_market_tick = AsyncMock()
        bus = RedisEventBus(url)
        register_worker_handlers(bot_manager, bus, feed=None, oms=None)
        await bus.start()
        try:
            candles = [_bar(1_700_000_100 + i * 60, 200 + i) for i in range(5)]
            await bus.publish(
                channels.BAR_CLOSE,
                {"symbol": "ETHUSDT", "candles": candles, "bar": candles[-2]},
            )
            for _ in range(20):
                if bot_manager.process_market_tick.await_count:
                    break
                await asyncio.sleep(0.1)
            self.assertGreaterEqual(bot_manager.process_market_tick.await_count, 1)
        finally:
            await bus.stop()


if __name__ == "__main__":
    unittest.main()
