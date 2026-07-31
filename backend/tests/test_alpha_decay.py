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
        self._candles_patcher = patch(
            "app.services.bots.alpha_decay.get_bot_candles", return_value=[],
        )
        self._candles_patcher.start()
        self.addCleanup(self._candles_patcher.stop)

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
    @patch("app.services.altdata.calendar.is_equity_rth_open", return_value=(True, None))
    async def test_filter_rejection_decay_alert(self, mock_rth, mock_emit, mock_expectations, mock_db):
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
    @patch("app.services.bots.alpha_decay.get_backtest_expectations")
    @patch("app.services.bots.alpha_decay.emit_notification", new_callable=AsyncMock)
    @patch("app.services.altdata.calendar.is_equity_rth_open", return_value=(False, "After market close"))
    async def test_filter_stale_skipped_outside_rth(self, mock_rth, mock_emit, mock_expectations, mock_db):
        """Post-close filter blocks must not trip Filter Stale for equities."""
        mock_expectations.return_value = (55.0, 1.5)
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        bot = self.bot_manager.active_bots["bot-1"]
        for _ in range(20):
            bot["signal_history"].append(False)

        res = await self.monitor.evaluate()
        decaying = res.get("decaying_bots") or []
        for row in decaying:
            self.assertFalse(any("Filter Stale" in r for r in row.get("reasons") or []))


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

    @patch("app.services.bots.alpha_decay.get_connection")
    @patch("app.services.bots.alpha_decay.get_backtest_expectations")
    @patch("app.services.bots.alpha_decay.emit_notification", new_callable=AsyncMock)
    @patch("app.services.bots.alpha_decay.get_bot_candles", return_value=[])
    @patch("app.services.bots.alpha_decay.ALPHA_DECAY_AUTO_PAUSE", True)
    @patch("app.services.bots.alpha_decay.ALPHA_DECAY_AUTO_RETRAIN", False)
    async def test_ml_model_check_uses_bot_timeframe(
        self, _candles, mock_emit, mock_expectations, mock_db,
    ):
        """HTF RL models live under SYMBOL__5M — age check must pass timeframe."""
        self.bot_manager.active_bots = {
            "bot-rl": {
                "id": "bot-rl",
                "symbol": "BTCUSDT",
                "timeframe": "5m",
                "strategy": "RL_PPO_AGENT",
                "status": "RUNNING",
                "config": {},
                "signal_history": deque(maxlen=20),
            }
        }
        mock_expectations.return_value = (55.0, 1.5)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        with patch(
            "app.services.bots.ml_retrain_scheduler.get_model_age_hours",
            return_value=2.0,
        ) as mock_age, patch(
            "app.services.bots.ml_retrain_scheduler.get_model_metadata",
            return_value={"metrics": {}},
        ):
            res = await self.monitor.evaluate()

        mock_age.assert_called()
        # Last (or any) call must include timeframe=5m
        self.assertTrue(
            any(
                call.kwargs.get("timeframe") == "5m"
                or (len(call.args) >= 3 and call.args[2] == "5m")
                for call in mock_age.call_args_list
            ),
            f"expected timeframe=5m in calls: {mock_age.call_args_list}",
        )
        self.assertEqual(res["decaying_bots"], [])
        self.assertEqual(res["paused_bots"], [])
        self.bot_manager.pause_bot.assert_not_awaited()
        mock_emit.assert_not_called()

    @patch("app.services.bots.alpha_decay.get_connection")
    @patch("app.services.bots.alpha_decay.get_backtest_expectations")
    @patch("app.services.bots.alpha_decay.emit_notification", new_callable=AsyncMock)
    @patch("app.services.bots.alpha_decay.get_bot_candles", return_value=[])
    @patch("app.services.bots.alpha_decay.ALPHA_DECAY_AUTO_PAUSE", True)
    @patch("app.services.bots.alpha_decay.ALPHA_DECAY_AUTO_RETRAIN", False)
    async def test_ml_model_missing_at_bot_timeframe_pauses(
        self, _candles, mock_emit, mock_expectations, mock_db,
    ):
        self.bot_manager.active_bots = {
            "bot-rl": {
                "id": "bot-rl",
                "symbol": "BTCUSDT",
                "timeframe": "5m",
                "strategy": "RL_PPO_AGENT",
                "status": "RUNNING",
                "config": {},
                "signal_history": deque(maxlen=20),
            }
        }
        mock_expectations.return_value = (55.0, 1.5)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        with patch(
            "app.services.bots.ml_retrain_scheduler.get_model_age_hours",
            return_value=None,
        ), patch(
            "app.services.bots.ml_retrain_scheduler.get_model_metadata",
            return_value=None,
        ):
            res = await self.monitor.evaluate()

        self.assertEqual(len(res["decaying_bots"]), 1)
        self.assertIn("bot-rl", res["paused_bots"])
        reason = res["decaying_bots"][0]["reasons"][0]
        self.assertIn("BTCUSDT @ 5m", reason)
        self.assertIn("RL_PPO_AGENT", reason)

