"""Unit tests for the Risk Sentinel Agent."""

import unittest
from datetime import datetime, timezone
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

    @patch("app.services.bots.risk_sentinel.list_bot_exposures")
    @patch("app.services.bots.risk_sentinel.bot_analytics.get_recent_consecutive_losses")
    @patch("app.services.bots.risk_sentinel.RISK_SENTINEL_MAX_VELOCITY", 3.0)
    @patch("app.services.bots.risk_sentinel.emit_notification", new_callable=AsyncMock)
    async def test_drawdown_velocity_breach(self, mock_emit, mock_losses, mock_exposures):
        """A single 4% mark jump warns; a second consecutive spike pauses."""
        mock_losses.return_value = 0
        mock_exposures.return_value = []
        snapshot_1 = FakeSnapshot(current_drawdown_pct=1.0)
        res_1 = await self.sentinel.evaluate(snapshot_1, self.oms, self.bot_manager)
        self.assertFalse(res_1["velocity_breached"])
        self.bot_manager.pause_bot.assert_not_awaited()

        snapshot_2 = FakeSnapshot(current_drawdown_pct=5.0)
        res_2 = await self.sentinel.evaluate(snapshot_2, self.oms, self.bot_manager)
        self.assertTrue(res_2["velocity_breached"])
        self.bot_manager.pause_bot.assert_not_awaited()
        mock_emit.assert_called()

        snapshot_3 = FakeSnapshot(current_drawdown_pct=9.0)
        res_3 = await self.sentinel.evaluate(snapshot_3, self.oms, self.bot_manager)
        self.assertTrue(res_3["velocity_breached"])
        self.bot_manager.pause_bot.assert_awaited_once_with("bot-1")
        self.bot_manager.log_bot_event.assert_awaited()

    @patch("app.services.bots.risk_sentinel.list_bot_exposures")
    @patch("app.services.bots.risk_sentinel.bot_analytics.get_recent_consecutive_losses")
    @patch("app.services.bots.risk_sentinel.RISK_SENTINEL_MAX_VELOCITY", 3.0)
    @patch("app.services.bots.risk_sentinel.emit_notification", new_callable=AsyncMock)
    async def test_drawdown_velocity_severe_pauses_immediately(
        self, mock_emit, mock_losses, mock_exposures,
    ):
        """A single move ≥ 2× the velocity limit still pauses the fleet."""
        mock_losses.return_value = 0
        mock_exposures.return_value = []
        await self.sentinel.evaluate(FakeSnapshot(1.0), self.oms, self.bot_manager)
        res = await self.sentinel.evaluate(FakeSnapshot(8.0), self.oms, self.bot_manager)
        self.assertTrue(res["velocity_breached"])
        self.bot_manager.pause_bot.assert_awaited_once_with("bot-1")

    @patch("app.services.bots.risk_sentinel.list_bot_exposures")
    @patch("app.services.bots.risk_sentinel.bot_analytics.get_recent_consecutive_losses")
    @patch("app.services.agent.desk_supervisor.auto_actions_enabled", return_value=True)
    @patch("app.services.bots.risk_sentinel.emit_notification", new_callable=AsyncMock)
    async def test_loss_streak_auto_pause(self, mock_emit, mock_auto, mock_get_losses, mock_exposures):
        """Active bots that reach their maximum loss streak should be auto-paused."""
        mock_get_losses.return_value = 5
        mock_exposures.return_value = []

        snapshot = FakeSnapshot(current_drawdown_pct=0.0)
        res = await self.sentinel.evaluate(snapshot, self.oms, self.bot_manager)
        
        self.assertEqual(res["streak_paused_count"], 1)
        self.bot_manager.pause_bot.assert_awaited_once_with("bot-1")
        self.bot_manager.log_bot_event.assert_awaited_once()
        mock_emit.assert_called_once()

    @patch("app.services.bots.risk_sentinel.bot_analytics.get_recent_consecutive_losses")
    @patch("app.services.bots.risk_sentinel.list_bot_exposures")
    @patch("app.services.bots.risk_sentinel._mark_prices")
    @patch("app.services.bots.risk_sentinel.summarize_basket_correlation")
    @patch("app.services.bots.risk_sentinel.emit_notification", new_callable=AsyncMock)
    async def test_correlation_exposure_warning(
        self, mock_emit, mock_corr, mock_prices, mock_exposures, mock_losses
    ):
        """Correlated positions on the same side exceeding group exposure limit should trigger warning."""
        mock_losses.return_value = 0
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

    @patch("app.services.bots.ml_walk_forward_validator.is_ml_strategy", return_value=True)
    @patch("app.services.bots.ml_feature_drift.drift_retrain_verdict")
    @patch("app.services.bots.risk_sentinel.list_bot_exposures")
    @patch("app.services.bots.risk_sentinel.bot_analytics.get_recent_consecutive_losses")
    @patch("app.services.bots.risk_sentinel.emit_notification", new_callable=AsyncMock)
    async def test_drift_alert_is_debounced(
        self, mock_emit, mock_losses, mock_exposures, mock_verdict, mock_is_ml,
    ):
        """The same drift WARN must not be written on every 30s sentinel tick."""
        mock_losses.return_value = 0
        mock_exposures.return_value = []
        mock_verdict.return_value = {
            "available": True,
            "assessment": "significant_drift",
            "overall_psi": 0.41,
            "n_live": 250,
            "baseline": "json",
        }
        self.bot_manager.active_bots = {
            "ad49d0d4": {
                "id": "ad49d0d4",
                "symbol": "SOLUSDT",
                "strategy": "TCN_MULTI_HORIZON",
                "status": "RUNNING",
                "created_at": "2020-01-01T00:00:00Z",
                "config": {},
            }
        }
        snapshot = FakeSnapshot(current_drawdown_pct=0.0)
        await self.sentinel.evaluate(snapshot, self.oms, self.bot_manager)
        await self.sentinel.evaluate(snapshot, self.oms, self.bot_manager)
        self.assertEqual(self.bot_manager.log_bot_event.await_count, 1)
        self.assertEqual(mock_emit.await_count, 1)

    @patch("app.services.bots.ml_walk_forward_validator.is_ml_strategy", return_value=True)
    @patch("app.services.bots.ml_feature_drift.drift_retrain_verdict")
    @patch("app.services.bots.risk_sentinel.list_bot_exposures")
    @patch("app.services.bots.risk_sentinel.bot_analytics.get_recent_consecutive_losses")
    @patch("app.services.bots.risk_sentinel.emit_notification", new_callable=AsyncMock)
    async def test_drift_alert_skipped_for_new_bot(
        self, mock_emit, mock_losses, mock_exposures, mock_verdict, mock_is_ml,
    ):
        """A just-deployed ML bot must not inherit leftover symbol×strategy drift."""
        mock_losses.return_value = 0
        mock_exposures.return_value = []
        mock_verdict.return_value = {
            "available": True,
            "assessment": "significant_drift",
            "overall_psi": 0.41,
            "n_live": 250,
            "baseline": "json",
        }
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.bot_manager.active_bots = {
            "ad49d0d4": {
                "id": "ad49d0d4",
                "symbol": "SOLUSDT",
                "strategy": "TCN_MULTI_HORIZON",
                "status": "RUNNING",
                "created_at": now_iso,
                "config": {},
            }
        }
        snapshot = FakeSnapshot(current_drawdown_pct=0.0)
        await self.sentinel.evaluate(snapshot, self.oms, self.bot_manager)
        self.bot_manager.log_bot_event.assert_not_awaited()
        mock_verdict.assert_not_called()
