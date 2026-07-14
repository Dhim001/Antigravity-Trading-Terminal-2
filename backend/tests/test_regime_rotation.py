"""Unit tests for the Regime Rotation Agent."""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from app.services.bots.regime_rotation import RegimeRotationAgent


class RegimeRotationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Mock BotManager
        self.bot_manager = MagicMock()
        self.bot_manager.oms.feed = MagicMock()
        self.bot_manager.active_bots = {
            "bot-1": {
                "id": "bot-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "strategy": "BRS_SCALPING",
                "status": "RUNNING",
                "config": {"regime_rotation_enabled": True},
            }
        }
        self.bot_manager._get_bot_position_size = MagicMock(return_value=0.0)
        self.bot_manager._get_bot_position = MagicMock(return_value={"avg_price": 150.0})
        self.bot_manager._mark_price = MagicMock(return_value=152.0)
        self.bot_manager._execute_order = AsyncMock()
        self.bot_manager.log_bot_event = AsyncMock()
        self.bot_manager._refresh_strategy_instance = MagicMock()

        self.agent = RegimeRotationAgent(self.bot_manager)

    @patch("app.services.bots.regime_rotation.get_bot_candles")
    @patch("app.services.bots.regime_rotation.get_connection")
    @patch("app.services.bots.regime_rotation.list_optimization_runs", return_value=[])
    @patch("app.services.bots.regime_rotation.emit_notification", new_callable=AsyncMock)
    async def test_regime_classification_trending(
        self, mock_emit, mock_opt_runs, mock_db, mock_candles
    ):
        """High ADX should classify as trending and rotate to SUPERTREND_ADX."""
        mock_candles.return_value = [{"time": i * 60, "close": 150.0} for i in range(100)]

        # Setup mock indicators DataFrame
        df = pd.DataFrame(
            [{
                "ADX_14": 35.0,  # Trending (> 25)
                "ATR_14": 1.0,
                "ATR_14_median_20": 1.0,  # Normal vol
            }]
        )
        self.bot_manager.screener.process_candles.return_value = df

        # Execute 3 times to satisfy hysteresis
        for _ in range(3):
            res = await self.agent.evaluate()

        # Verify
        self.assertEqual(len(res["rotations"]), 1)
        rotation = res["rotations"][0]
        self.assertEqual(rotation["to_strategy"], "SUPERTREND_ADX")
        self.assertEqual(rotation["regime"], "trending")

        # In-memory bot updated
        bot = self.bot_manager.active_bots["bot-1"]
        self.assertEqual(bot["strategy"], "SUPERTREND_ADX")
        self.bot_manager._refresh_strategy_instance.assert_called_once_with("bot-1")
        mock_emit.assert_called_once()

    @patch("app.services.bots.regime_rotation.get_bot_candles")
    @patch("app.services.bots.regime_rotation.get_connection")
    @patch("app.services.bots.regime_rotation.list_optimization_runs", return_value=[])
    @patch("app.services.bots.regime_rotation.emit_notification", new_callable=AsyncMock)
    async def test_regime_classification_ranging(
        self, mock_emit, mock_opt_runs, mock_db, mock_candles
    ):
        """Low ADX should classify as ranging and rotate to BRS_SCALPING."""
        # Bot currently running SUPERTREND_ADX
        self.bot_manager.active_bots["bot-1"]["strategy"] = "SUPERTREND_ADX"
        mock_candles.return_value = [{"time": i * 60, "close": 150.0} for i in range(100)]

        # Setup mock indicators DataFrame
        df = pd.DataFrame(
            [{
                "ADX_14": 15.0,  # Ranging (<= 25)
                "ATR_14": 1.0,
                "ATR_14_median_20": 1.0,  # Normal vol
            }]
        )
        self.bot_manager.screener.process_candles.return_value = df

        # Execute 3 times to satisfy hysteresis
        for _ in range(3):
            res = await self.agent.evaluate()

        # Verify
        self.assertEqual(len(res["rotations"]), 1)
        rotation = res["rotations"][0]
        self.assertEqual(rotation["to_strategy"], "BRS_SCALPING")
        self.assertEqual(rotation["regime"], "ranging")

        # In-memory bot updated
        bot = self.bot_manager.active_bots["bot-1"]
        self.assertEqual(bot["strategy"], "BRS_SCALPING")
        self.bot_manager._refresh_strategy_instance.assert_called_once_with("bot-1")

    @patch("app.services.bots.regime_rotation.get_bot_candles")
    @patch("app.services.bots.regime_rotation.get_connection")
    @patch("app.services.bots.regime_rotation.list_optimization_runs", return_value=[])
    @patch("app.services.bots.regime_rotation.emit_notification", new_callable=AsyncMock)
    async def test_regime_classification_elevated_vol(
        self, mock_emit, mock_opt_runs, mock_db, mock_candles
    ):
        """High ATR ratio should classify as elevated_vol and rotate to VWAP_PULLBACK."""
        mock_candles.return_value = [{"time": i * 60, "close": 150.0} for i in range(100)]

        # Setup mock indicators DataFrame
        df = pd.DataFrame(
            [{
                "ADX_14": 15.0,
                "ATR_14": 2.0,
                "ATR_14_median_20": 1.0,  # Ratio = 2.0 >= 1.5
            }]
        )
        self.bot_manager.screener.process_candles.return_value = df

        # Execute 3 times to satisfy hysteresis
        for _ in range(3):
            res = await self.agent.evaluate()

        # Verify
        self.assertEqual(len(res["rotations"]), 1)
        rotation = res["rotations"][0]
        self.assertEqual(rotation["to_strategy"], "VWAP_PULLBACK")
        self.assertEqual(rotation["regime"], "elevated_vol")

    @patch("app.services.bots.regime_rotation.get_bot_candles")
    @patch("app.services.bots.regime_rotation.get_connection")
    @patch("app.services.bots.regime_rotation.list_optimization_runs")
    @patch("app.services.bots.regime_rotation.emit_notification", new_callable=AsyncMock)
    async def test_bot_strategy_rotation_flatten(
        self, mock_emit, mock_opt_runs, mock_db, mock_candles
    ):
        """If flatten is enabled and the bot holds a position, it should close it first."""
        mock_candles.return_value = [{"time": i * 60, "close": 150.0} for i in range(100)]
        
        # Position size = 10 (Long)
        self.bot_manager._get_bot_position_size.return_value = 10.0

        # Setup mock indicators DataFrame for Trending
        df = pd.DataFrame(
            [{
                "ADX_14": 30.0,
                "ATR_14": 1.0,
                "ATR_14_median_20": 1.0,
            }]
        )
        self.bot_manager.screener.process_candles.return_value = df

        # Mock optimization config
        mock_opt_runs.return_value = [
            {"strategy": "SUPERTREND_ADX", "best_config": {"multiplier": 3.0}}
        ]

        # Execute 3 times to satisfy hysteresis
        for _ in range(3):
            res = await self.agent.evaluate()

        # Verify position was closed
        self.assertEqual(res["flattened_count"], 1)
        self.bot_manager._execute_order.assert_awaited_once()
        args = self.bot_manager._execute_order.call_args[0]
        self.assertEqual(args[1], "SELL")  # opposite of LONG (size=10.0)
        self.assertEqual(args[2], 10.0)    # quantity

        # Verify bot was updated to SUPERTREND_ADX and loaded custom sweep config
        bot = self.bot_manager.active_bots["bot-1"]
        self.assertEqual(bot["strategy"], "SUPERTREND_ADX")
        self.assertEqual(bot["config"]["multiplier"], 3.0)
