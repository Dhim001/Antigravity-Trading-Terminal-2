"""Paper OMS tick helper — limit fills and SL/TP broadcast (Sim + LIVE_MASSIVE)."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.bots.paper_oms import run_paper_oms_tick


class PaperOmsTickTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_fills_skips_broadcast(self) -> None:
        oms = MagicMock()
        oms.match_pending_orders.return_value = 0
        oms.check_sl_tp_triggers.return_value = (0, [], [])

        with patch(
            "app.services.bots.paper_oms.publish_post_trade_bundle",
            new_callable=AsyncMock,
        ) as mock_bundle:
            result = await run_paper_oms_tick(oms, MagicMock(), MagicMock())
            self.assertFalse(result)
            mock_bundle.assert_not_called()

    async def test_limit_fill_broadcasts_account(self) -> None:
        oms = MagicMock()
        oms.match_pending_orders.return_value = 1
        oms.check_sl_tp_triggers.return_value = (0, [], [])
        oms.get_account_data.return_value = {"balances": {}}
        oms.get_trade_history.return_value = []

        with patch(
            "app.services.bots.paper_oms.publish_post_trade_bundle",
            new_callable=AsyncMock,
        ) as mock_bundle:
            result = await run_paper_oms_tick(oms, MagicMock(), MagicMock())
            self.assertTrue(result)
            mock_bundle.assert_called_once()

    async def test_sl_tp_exit_invokes_bot_manager(self) -> None:
        oms = MagicMock()
        oms.match_pending_orders.return_value = 0
        oms.check_sl_tp_triggers.return_value = (1, ["SL hit"], [{"bot_id": "b1"}])
        oms.get_account_data.return_value = {"balances": {}}
        oms.get_trade_history.return_value = []
        bot_manager = MagicMock()
        bot_manager.handle_sl_tp_exits = AsyncMock()
        manager = MagicMock()
        manager.broadcast = AsyncMock()

        with patch(
            "app.services.bots.paper_oms.publish_post_trade_bundle",
            new_callable=AsyncMock,
        ):
            result = await run_paper_oms_tick(oms, bot_manager, manager)
            self.assertTrue(result)
            bot_manager.handle_sl_tp_exits.assert_awaited_once_with([{"bot_id": "b1"}])


if __name__ == "__main__":
    unittest.main()
