"""Tests for typed outbound message builders."""

import unittest
from unittest.mock import AsyncMock

from app.api.outbound import (
    account_update,
    bot_log,
    error,
    frame,
    market_update,
    publish,
    publish_post_trade_bundle,
)
from app.api.protocol import MessageType


class TestOutboundFrames(unittest.TestCase):
    def test_frame_account_update(self):
        payload = account_update({"balances": {}})
        self.assertEqual(payload["type"], MessageType.ACCOUNT_UPDATE)
        self.assertEqual(payload["data"], {"balances": {}})

    def test_frame_error(self):
        payload = error("boom")
        self.assertEqual(payload["type"], MessageType.ERROR)
        self.assertEqual(payload["message"], "boom")

    def test_bot_log_shape(self):
        payload = bot_log("bot-1", "INFO", "hello")
        self.assertEqual(payload["type"], MessageType.BOT_LOG)
        self.assertEqual(payload["data"]["bot_id"], "bot-1")

    def test_market_update(self):
        payload = market_update({"AAPL": {"price": 100}})
        self.assertEqual(payload["type"], MessageType.MARKET_UPDATE)


class TestOutboundPublish(unittest.IsolatedAsyncioTestCase):
    async def test_publish_noop_when_callback_missing(self):
        await publish(None, account_update({}))

    async def test_publish_post_trade_bundle(self):
        cb = AsyncMock()
        await publish_post_trade_bundle(cb, {"x": 1}, [{"id": 1}])
        self.assertEqual(cb.await_count, 2)
        types = [call.args[0]["type"] for call in cb.await_args_list]
        self.assertEqual(types, [MessageType.ACCOUNT_UPDATE, MessageType.TRADE_HISTORY])


if __name__ == "__main__":
    unittest.main()
