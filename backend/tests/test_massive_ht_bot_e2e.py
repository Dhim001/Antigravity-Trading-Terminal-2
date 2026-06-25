"""End-to-end: LIVE_MASSIVE scheduler → HT bar-close → native REST candles (no 1m resample)."""

from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TERMINAL_MODE", "LIVE_MASSIVE")
os.environ["DATABASE_URL"] = ""

_TEST_DIR = tempfile.mkdtemp()
import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "massive_ht_e2e.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH
app_config.TERMINAL_MODE = "LIVE_MASSIVE"

from app.database import get_connection, init_db  # noqa: E402
from app.services.bots.candle_source import get_bot_candles  # noqa: E402
from app.services.bots.massive_scheduler import (  # noqa: E402
    _ht_last_1m_bar,
    run_massive_bot_tick,
)


def _reset_db() -> None:
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


def _ht_series(base: int = 1_700_000_000, bars: int = 60) -> list[dict]:
    return [
        {
            "time": base + i * 300,
            "open": 100.0 + i * 0.01,
            "high": 101.0 + i * 0.01,
            "low": 99.0 + i * 0.01,
            "close": 100.5 + i * 0.01,
            "volume": 10,
        }
        for i in range(bars)
    ]


class MassiveHtBotE2ETests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _reset_db()
        from app.services.bots.manager import BotManagerService

        self.oms = MagicMock()
        self.manager = BotManagerService(self.oms, MagicMock(), AsyncMock())
        self.bot_id = str(uuid.uuid4())
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.bot_id, "ema_cross", "BTCUSDT", "5m", "RUNNING", 5000, "{}"),
        )
        conn.commit()
        conn.close()
        self.manager.active_bots = {
            self.bot_id: {
                "id": self.bot_id,
                "symbol": "BTCUSDT",
                "status": "RUNNING",
                "timeframe": "5m",
                "execution_mode": "BAR_CLOSE",
                "strategy_instance": MagicMock(),
                "config": {},
            },
        }
        self.manager._evaluate_bar_close_bots = AsyncMock()
        self.manager._bar_tracker.check = MagicMock(return_value=True)
        self.manager._warm_chart_agent_caches = AsyncMock()

    def test_get_bot_candles_prefers_native_ht_over_resample(self) -> None:
        native = _ht_series()
        feed = MagicMock()
        feed.fetch_ht_candles.return_value = native
        feed.get_candles.return_value = []

        with patch("app.services.bots.candle_source.TERMINAL_MODE", "LIVE_MASSIVE"):
            out = get_bot_candles("BTCUSDT", feed, timeframe="5m", min_bars=50)

        feed.fetch_ht_candles.assert_called_once()
        self.assertGreaterEqual(len(out), 50)
        self.assertEqual(out[-1]["time"], native[-1]["time"])

    async def test_scheduler_to_ht_eval_uses_native_candles(self) -> None:
        ht = _ht_series()
        feed = MagicMock()
        feed.symbols = ["BTCUSDT"]
        feed.get_market_data.return_value = {"price": 100.5}
        feed.get_candles.return_value = [{"time": 1_700_000_000, "close": 100.0}]

        _ht_last_1m_bar.clear()

        with (
            patch("app.services.bots.massive_scheduler.ALLOW_LIVE_BOTS", True),
            patch("app.services.bots.massive_scheduler.is_live_massive", return_value=True),
            patch("app.services.bots.manager.ALLOW_LIVE_BOTS", True),
            patch("app.services.bots.manager.is_live_massive", return_value=True),
            patch("app.services.bots.massive_scheduler.run_paper_oms_tick", new_callable=AsyncMock),
            patch("app.services.bots.manager.get_bot_candles", return_value=ht) as mock_candles,
        ):
            await run_massive_bot_tick(
                self.manager,
                feed,
                MagicMock(),
                MagicMock(),
                last_prices={},
            )

        mock_candles.assert_called_with("BTCUSDT", feed, timeframe="5m")
        self.manager._evaluate_bar_close_bots.assert_awaited_once()
        eval_args, _ = self.manager._evaluate_bar_close_bots.await_args
        self.assertEqual(eval_args[0], "BTCUSDT")
        self.assertEqual(eval_args[1], "5m")
        self.assertGreaterEqual(len(eval_args[2]), 50)


if __name__ == "__main__":
    unittest.main()
