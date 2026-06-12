"""Tests for centralized WebSocket action router."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.context import RequestContext
from app.api.protocol import Action
from app.api.router import ROUTES, dispatch, ensure_routes_loaded, list_routes


class TestRouteRegistry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_routes_loaded()

    def test_all_actions_registered(self):
        expected = {a.value for a in Action}
        registered = set(ROUTES.keys())
        self.assertEqual(expected, registered)

    def test_no_duplicate_routes(self):
        routes = list_routes()
        self.assertEqual(len(routes), len(Action))

    def test_route_tags(self):
        routes = list_routes()
        self.assertIn("trading", routes[Action.PLACE_ORDER.value].tags)
        self.assertIn("bots", routes[Action.BOT_CREATE.value].tags)


class TestDispatch(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        ensure_routes_loaded()

    def _make_ctx(self, action, message=None):
        manager = MagicMock()
        manager.send_to = AsyncMock()
        manager.broadcast = AsyncMock()
        oms = MagicMock()
        oms.get_account_data.return_value = {"balance": 10000}
        oms.get_trade_history.return_value = []
        bot_manager = MagicMock()
        bot_manager.list_bots_public.return_value = []
        return RequestContext(
            websocket=object(),
            manager=manager,
            oms=oms,
            bot_manager=bot_manager,
            backtester=None,
            message=message or {"action": action},
            action=action,
        )

    async def test_unknown_action(self):
        ctx = self._make_ctx("not_a_real_action")
        await dispatch(ctx)
        ctx.manager.send_to.assert_awaited_once()
        payload = ctx.manager.send_to.await_args.args[1]
        self.assertEqual(payload["type"], "error")
        self.assertIn("Unknown action", payload["message"])

    async def test_missing_action(self):
        ctx = self._make_ctx(None, message={})
        await dispatch(ctx)
        payload = ctx.manager.send_to.await_args.args[1]
        self.assertEqual(payload["type"], "error")
        self.assertEqual(payload["message"], "Missing action")

    async def test_get_account(self):
        ctx = self._make_ctx(Action.GET_ACCOUNT.value)
        await dispatch(ctx)
        ctx.manager.send_to.assert_awaited_once()
        payload = ctx.manager.send_to.await_args.args[1]
        self.assertEqual(payload["type"], "account_update")
        self.assertEqual(payload["data"], {"balance": 10000})

    async def test_get_history(self):
        ctx = self._make_ctx(Action.GET_HISTORY.value)
        await dispatch(ctx)
        payload = ctx.manager.send_to.await_args.args[1]
        self.assertEqual(payload["type"], "trade_history")
        self.assertEqual(payload["data"], [])

    async def test_bot_get_all(self):
        ctx = self._make_ctx(Action.BOT_GET_ALL.value)
        ctx.bot_manager.list_bots_public.return_value = [{"id": "b1"}]
        await dispatch(ctx)
        payload = ctx.manager.send_to.await_args.args[1]
        self.assertEqual(payload["type"], "bots_update")
        self.assertEqual(payload["data"], [{"id": "b1"}])

    @patch("app.api.router.TERMINAL_MODE", "LIVE_BINANCE")
    async def test_sim_only_blocks_seed_balance(self):
        ctx = self._make_ctx(
            Action.ADMIN_SEED_BALANCE.value,
            message={"action": Action.ADMIN_SEED_BALANCE.value, "asset": "USD", "amount": 100},
        )
        await dispatch(ctx)
        payload = ctx.manager.send_to.await_args.args[1]
        self.assertEqual(payload["type"], "order_result")
        self.assertEqual(payload["data"]["status"], "error")
        self.assertIn("live trading", payload["data"]["message"].lower())

    @patch("app.api.router.TERMINAL_MODE", "LIVE_BINANCE")
    async def test_sim_only_blocks_reset_system(self):
        ctx = self._make_ctx(Action.ADMIN_RESET_SYSTEM.value)
        await dispatch(ctx)
        payload = ctx.manager.send_to.await_args.args[1]
        self.assertEqual(payload["data"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
