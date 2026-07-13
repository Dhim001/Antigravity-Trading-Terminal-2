"""Unit tests for the Risk Sentinel Agent."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.bots.risk_sentinel import RiskSentinel


class FakeSnapshot:
    def __init__(self, current_drawdown_pct: float, account_equity: float = 10000.0):
        self.current_drawdown_pct = current_drawdown_pct
        self.account_equity = account_equity


class FakeOms:
    def __init__(self):
        self.feed = MagicMock()


class RiskSentinelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sentinel = RiskSentinel()
        self.oms = FakeOms()

        # Mock BotManager
        self.bot_manager = MagicMock()
        self.bot_manager.active_bots = {
            "bot-1": {
                "id": "bot-1",
                "symbol": "AAPL",
                "status": "RUNNING",
                "config": {"max_consecutive_losses": 5},
            }
        }
        self.bot_manager.pause_bot = AsyncMock()
        self.bot_manager.log_bot_event = AsyncMock()

    @patch("app.services.bots.risk_sentinel.RISK_SENTINEL_MAX_VELOCITY", 3.0)
    @patch("app.services.bots.risk_sentinel.emit_notification", new_callable=AsyncMock)
    async def test_drawdown_velocity_breach(self, mock_emit):
        """Drawdown velocity breach should trigger alert notification and pause active bots."""
        # First check — no velocity check since history len < 2
        snapshot_1 = FakeSnapshot(current_drawdown_pct=1.0)
        res_1 = await self.sentinel.evaluate(snapshot_1, self.oms, self.bot_manager)
        self.assertFalse(res_1["velocity_breached"])
        self.bot_manager.pause_bot.assert_not_awaited()

        # Second check — drawdown jumps from 1% to 5% (diff = 4% >= 3% limit)
        snapshot_2 = FakeSnapshot(current_drawdown_pct=5.0)
        res_2 = await self.sentinel.evaluate(snapshot_2, self.oms, self.bot_manager)
        self.assertTrue(res_2["velocity_breached"])
        
        # Verify notification was sent and bot was paused
        mock_emit.assert_called_once()
        self.bot_manager.pause_bot.assert_awaited_once_with("bot-1")
        self.bot_manager.log_bot_event.assert_awaited_once()

    @patch("app.services.bots.risk_sentinel.bot_analytics.get_recent_consecutive_losses")
    @patch("app.services.bots.risk_sentinel.emit_notification", new_callable=AsyncMock)
    async def test_loss_streak_auto_pause(self, mock_emit, mock_get_losses):
        """Active bots that reach their maximum loss streak should be auto-paused."""
        # Mock streak to be 5 (equal to max_streak in bot config)
        mock_get_losses.return_value = 5

        snapshot = FakeSnapshot(current_drawdown_pct=0.0)
        res = await self.sentinel.evaluate(snapshot, self.oms, self.bot_manager)
        
        self.assertEqual(res["streak_paused_count"], 1)
        self.bot_manager.pause_bot.assert_awaited_once_with("bot-1")
        self.bot_manager.log_bot_event.assert_awaited_once()
        mock_emit.assert_called_once()

    @patch("app.services.bots.risk_sentinel.list_bot_exposures")
    @patch("app.services.bots.risk_sentinel._mark_prices")
    @patch("app.services.bots.risk_sentinel.summarize_basket_correlation")
    @patch("app.services.bots.risk_sentinel.emit_notification", new_callable=AsyncMock)
    async def test_correlation_exposure_warning(
        self, mock_emit, mock_corr, mock_prices, mock_exposures
    ):
        """Correlated positions on the same side exceeding group exposure limit should trigger warning."""
        # 2 active positions
        mock_exposures.return_value = [
            {"bot_id": "bot-1", "symbol": "AAPL", "size": 100.0, "avg_price": 150.0},
            {"bot_id": "bot-2", "symbol": "MSFT", "size": 50.0, "avg_price": 300.0},
        ]
        # Mark prices
        mock_prices.return_value = {"AAPL": 200.0, "MSFT": 400.0}
        
        # High correlation of 0.8
        mock_corr.return_value = {
            "high_pairs": [{"a": "AAPL", "b": "MSFT", "correlation": 0.8}]
        }

        # Combined exposure = 100 * 200 + 50 * 400 = 40000
        # Equity = 50000 => 80% (exceeds limit 40%)
        snapshot = FakeSnapshot(current_drawdown_pct=0.0, account_equity=50000.0)

        res = await self.sentinel.evaluate(snapshot, self.oms, self.bot_manager)
        
        self.assertEqual(len(res["correlation_warnings"]), 1)
        self.assertEqual(res["correlation_warnings"][0]["a"], "AAPL")
        self.assertEqual(res["correlation_warnings"][0]["b"], "MSFT")
        mock_emit.assert_called_once()
