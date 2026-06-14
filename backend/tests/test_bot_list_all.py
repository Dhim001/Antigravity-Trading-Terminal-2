"""Tests for bot_list_all (active + stopped bots)."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from app.api.context import RequestContext
from app.api.handlers import bots  # noqa: F401 — register routes
from app.api.protocol import Action
from app.api.router import dispatch
from app.database import get_connection, init_db


class TestBotListAll(unittest.TestCase):
    def setUp(self):
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bot_trades")
        cursor.execute("DELETE FROM bots")
        cursor.execute(
            """
            INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config)
            VALUES ('bot-active', 'MACD_RSI', 'BTCUSDT', '1m', 'STOPPED', 1000, '{}'),
                   ('bot-old', 'VWAP_PULLBACK', 'AAPL', '1m', 'STOPPED', 500, '{}')
            """
        )
        conn.commit()
        conn.close()

    def test_list_all_bots_public_includes_stopped(self):
        from app.services.bots.manager import BotManagerService

        oms = MagicMock()
        oms.get_account_data.return_value = {"balances": {}, "positions": {}, "orders": []}
        mgr = BotManagerService(oms, MagicMock(), broadcast_cb=None)
        rows = mgr.list_all_bots_public(limit=50)
        ids = {r["id"] for r in rows}
        self.assertIn("bot-active", ids)
        self.assertIn("bot-old", ids)
        self.assertEqual(len(rows), 2)

    async def _run_dispatch(self, action, message=None):
        ws = AsyncMock()
        manager = MagicMock()
        manager.send_to = AsyncMock()
        oms = MagicMock()
        oms.get_account_data.return_value = {"balances": {}, "positions": {}, "orders": []}
        from app.services.bots.manager import BotManagerService

        bot_manager = BotManagerService(oms, MagicMock(), broadcast_cb=None)
        ctx = RequestContext(
            websocket=ws,
            message=message or {},
            action=action,
            manager=manager,
            oms=oms,
            bot_manager=bot_manager,
            backtester=None,
        )
        await dispatch(ctx)
        return manager.send_to.call_args[0][1]

    def test_bot_list_all_handler(self):
        payload = asyncio.run(self._run_dispatch(Action.BOT_LIST_ALL))
        self.assertEqual(payload["type"], "bots_history")
        data = payload["data"]
        self.assertEqual(len(data), 2)


if __name__ == "__main__":
    unittest.main()
