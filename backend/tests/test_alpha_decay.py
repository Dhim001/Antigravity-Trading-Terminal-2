"""Unit tests for the Alpha Decay Monitor Agent."""

import json
import unittest
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from app.services.bots.alpha_decay import AlphaDecayMonitor


class AlphaDecayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Mock BotManager
        self.bot_manager = MagicMock()
        self.bot_manager.oms.feed = MagicMock()
        self.bot_manager.active_bots = {
            "bot-1": {
                "id": "bot-1",
                "symbol": "AAPL",
                "timeframe": "1m",
                "strategy": "SUPERTREND_ADX",
                "status": "RUNNING",
                "config": {"alpha_decay_monitor_disabled": False},
                "signal_history": deque(maxlen=20),
            }
        }
        self.bot_manager.pause_bot = AsyncMock()
        self.bot_manager.log_bot_event = AsyncMock()
        self.bot_manager.screener = MagicMock()

        self.monitor = AlphaDecayMonitor(self.bot_manager)

    @patch("app.services.bots.alpha_decay.get_connection")
    @patch("app.services.bots.alpha_decay.get_backtest_expectations")
    @patch("app.services.bots.alpha_decay.emit_notification", new_callable=AsyncMock)
    async def test_win_rate_decay_alert(self, mock_emit, mock_expectations, mock_db):
        """Win rate dropping >15% below expectations triggers pause and alert."""
        mock_expectations.return_value = (60.0, 1.5)  # Expected 60% win rate

        # Reconstruct last 20 exits: 16 losses, 4 wins (20% win rate)
        trades = []
        for i in range(16):
            trades.append((-10.0, f"2026-07-11T12:{i:02d}:00Z"))
        for i in range(4):
            trades.append((20.0, f"2026-07-11T13:{i:02d}:00Z"))

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = trades
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        res = await self.monitor.evaluate()

        self.assertEqual(len(res["decaying_bots"]), 1)
        self.assertEqual(res["decaying_bots"][0]["bot_id"], "bot-1")
        self.assertIn("bot-1", res["paused_bots"])
        self.bot_manager.pause_bot.assert_awaited_once_with("bot-1")
        mock_emit.assert_called_once()

    @patch("app.services.bots.alpha_decay.get_connection")
    @patch("app.services.bots.alpha_decay.get_backtest_expectations")
    @patch("app.services.bots.alpha_decay.emit_notification", new_callable=AsyncMock)
    async def test_sharpe_decay_alert(self, mock_emit, mock_expectations, mock_db):
        """Sharpe ratio dropping <50% of expected Sharpe triggers decay alert."""
        mock_expectations.return_value = (55.0, 2.0)  # Expected Sharpe = 2.0

        # Sharpe ratio returns close to zero/negative
        trades = []
        for i in range(15):
            trades.append((-1.0, f"2026-07-11T12:{i:02d}:00Z"))
        for i in range(15):
            trades.append((1.05, f"2026-07-11T13:{i:02d}:00Z"))

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = trades
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        res = await self.monitor.evaluate()

        self.assertEqual(len(res["decaying_bots"]), 1)
        self.assertIn("bot-1", res["paused_bots"])

    @patch("app.services.bots.alpha_decay.get_connection")
    @patch("app.services.bots.alpha_decay.get_bot_candles")
    @patch("app.services.bots.alpha_decay.get_backtest_expectations")
    @patch("app.services.bots.alpha_decay.emit_notification", new_callable=AsyncMock)
    async def test_regime_mismatch_alert(self, mock_emit, mock_expectations, mock_candles, mock_db):
        """Trending strategy in ranging market (trending bars < 30%) triggers regime mismatch alert."""
        mock_expectations.return_value = (55.0, 1.5)
        mock_candles.return_value = [{"time": i, "close": 100.0} for i in range(100)]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []  # No trades to bypass win rate/Sharpe check
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        # Mock candles show ADX is consistently low (ADX = 15 -> ranging)
        df = pd.DataFrame([{"ADX_14": 15.0} for _ in range(50)])
        self.bot_manager.screener.process_candles.return_value = df

        res = await self.monitor.evaluate()

        self.assertEqual(len(res["decaying_bots"]), 1)
        reasons = res["decaying_bots"][0]["reasons"]
        self.assertTrue(any("Regime Mismatch" in r for r in reasons))

    @patch("app.services.bots.alpha_decay.get_connection")
    @patch("app.services.bots.alpha_decay.get_backtest_expectations")
    @patch("app.services.bots.alpha_decay.emit_notification", new_callable=AsyncMock)
    async def test_filter_rejection_decay_alert(self, mock_emit, mock_expectations, mock_db):
        """Rejection rate of >=80% triggers consecutive rejections alert."""
        mock_expectations.return_value = (55.0, 1.5)
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        # 16 blocked (False), 4 accepted (True)
        bot = self.bot_manager.active_bots["bot-1"]
        for _ in range(16):
            bot["signal_history"].append(False)
        for _ in range(4):
            bot["signal_history"].append(True)

        res = await self.monitor.evaluate()

        self.assertEqual(len(res["decaying_bots"]), 1)
        reasons = res["decaying_bots"][0]["reasons"]
        self.assertTrue(any("Filter Stale" in r for r in reasons))

    @patch("app.services.bots.alpha_decay.get_connection")
    @patch("app.services.bots.alpha_decay.get_meta_label_store")
    @patch("app.services.bots.alpha_decay.train_meta_label_model")
    @patch("app.services.bots.alpha_decay.get_backtest_expectations")
    @patch("app.services.bots.alpha_decay.emit_notification", new_callable=AsyncMock)
    async def test_confidence_drift_alert(self, mock_emit, mock_expectations, mock_retrain, mock_store, mock_db):
        """Confidence drift below training win rate triggers alert and retrain model."""
        mock_expectations.return_value = (55.0, 1.5)
        mock_retrain.return_value = {"ok": True}

        # Mock metadata
        mock_meta = MagicMock()
        mock_meta.get_metadata.return_value = {"metrics": {"train_win_rate": 0.65}}
        mock_store.return_value = mock_meta

        # Mock database cursor to return low confidence entry snapshots
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # 1st query: exits (returns empty)
        # 2nd query: entry snapshots (returns 5 entry snapshots with 0.40 confidence)
        snapshots = [
            (json.dumps({"confidence": 0.40}),)
            for _ in range(5)
        ]
        
        mock_cursor.fetchall.side_effect = [[], snapshots]
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        res = await self.monitor.evaluate()

        self.assertEqual(len(res["decaying_bots"]), 1)
        self.assertIn("bot-1", res["retrained_models"])
        mock_retrain.assert_called_once_with("bot-1")
