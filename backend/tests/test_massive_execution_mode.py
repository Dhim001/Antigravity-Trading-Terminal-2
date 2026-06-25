"""LIVE_MASSIVE execution semantics — paper OMS, no reconcile, HT REST bar-close."""

from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""

_TEST_DIR = tempfile.mkdtemp()
import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "massive_exec_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH

from app.database import get_connection, init_db  # noqa: E402
from app.services.bots import analytics as bot_analytics  # noqa: E402
from app.services.bots.execution_mode import execution_mode_label, uses_paper_oms  # noqa: E402


def _reset_db() -> None:
    db_conn.DB_PATH = os.path.join(_TEST_DIR, "massive_exec_test.db")
    app_config.DB_PATH = db_conn.DB_PATH
    db_conn._pool = None
    path = db_conn.DB_PATH
    if os.path.exists(path):
        try:
            os.remove(path)
        except PermissionError:
            conn = get_connection()
            cursor = conn.cursor()
            for table in ("bot_pending_fills", "bot_trades", "bots"):
                cursor.execute(f"DELETE FROM {table}")
            conn.commit()
            conn.close()
    init_db()


class ExecutionModeLabelTests(unittest.TestCase):
    @patch("app.services.bots.execution_mode.TERMINAL_MODE", "LIVE_MASSIVE")
    def test_live_massive_is_paper(self) -> None:
        self.assertTrue(uses_paper_oms())
        self.assertEqual(execution_mode_label(), "paper")

    @patch("app.services.bots.execution_mode.TERMINAL_MODE", "LIVE_IB")
    def test_broker_mode(self) -> None:
        self.assertFalse(uses_paper_oms())
        self.assertEqual(execution_mode_label(), "broker")


class MassiveReconcileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _reset_db()
        self.oms = MagicMock()
        self.oms.get_trade_history.return_value = []
        from app.services.bots.manager import BotManagerService

        self.manager = BotManagerService(self.oms, MagicMock(), AsyncMock())

    @patch("app.services.bots.manager.uses_paper_oms", return_value=True)
    async def test_reconcile_skipped_for_paper_modes(self, _paper) -> None:
        bot_id = str(uuid.uuid4())
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (bot_id, "ema_cross", "AAPL", "1m", "RUNNING", 10000, "{}"),
        )
        conn.commit()
        conn.close()

        bot_analytics.record_pending_fill(
            bot_id, "ord-1", "AAPL", "BUY", 1.0, 100.0, signal_id="sig-1", is_exit=False,
        )
        confirmed = await self.manager.reconcile_pending_fills()
        self.assertEqual(confirmed, 0)
        self.oms.get_trade_history.assert_not_called()
        self.assertEqual(len(bot_analytics.list_pending_fills()), 1)


class MassiveMarketTickTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _reset_db()
        from app.services.bots.manager import BotManagerService

        self.manager = BotManagerService(MagicMock(), MagicMock(), AsyncMock())
        self.bot_id = str(uuid.uuid4())
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.bot_id, "ema_cross", "AAPL", "1h", "RUNNING", 10000, "{}"),
        )
        conn.commit()
        conn.close()
        self.manager.active_bots = {
            self.bot_id: {
                "id": self.bot_id,
                "symbol": "AAPL",
                "status": "RUNNING",
                "timeframe": "1h",
                "execution_mode": "BAR_CLOSE",
                "strategy_instance": MagicMock(),
                "config": {},
            },
        }
        self.manager._evaluate_bar_close_bots = AsyncMock()
        self.manager._bar_tracker.check = MagicMock(return_value=True)

    @patch("app.services.bots.manager.ALLOW_LIVE_BOTS", True)
    @patch("app.services.bots.manager.is_live_massive", return_value=True)
    async def test_process_market_tick_without_feed_skips_resample(self, _live) -> None:
        base = 1_700_000_000
        ohlcv_1m = [{"time": base + i * 60, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1} for i in range(120)]
        await self.manager.process_market_tick("AAPL", ohlcv_1m, feed=None)
        self.manager._evaluate_bar_close_bots.assert_not_called()

    async def test_process_massive_ht_uses_native_rest(self) -> None:
        base = 1_700_000_000
        ht = [
            {"time": base, "open": 1, "high": 2, "low": 0.5, "close": 1.0, "volume": 1},
            {"time": base + 3600, "open": 1, "high": 2, "low": 0.5, "close": 1.2, "volume": 1},
            {"time": base + 7200, "open": 1.2, "high": 2, "low": 0.5, "close": 1.3, "volume": 1},
        ]
        feed = MagicMock()
        with (
            patch("app.services.bots.manager.is_live_massive", return_value=True),
            patch("app.services.bots.manager.ALLOW_LIVE_BOTS", True),
            patch("app.services.bots.manager.get_bot_candles", return_value=ht) as mock_candles,
        ):
            await self.manager.process_massive_ht_bar_close("AAPL", feed, {"1h"})
        mock_candles.assert_called_with("AAPL", feed, timeframe="1h")
        self.manager._evaluate_bar_close_bots.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
