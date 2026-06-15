"""P2 deferred backend: per-bot virtual TP, parquet dual-write, bot config updates."""

import os
import tempfile
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""

_TEST_DIR = tempfile.mkdtemp()
import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "p2_features_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH

from app.database import get_connection, init_db  # noqa: E402
from app.services.bots import positions as bot_positions  # noqa: E402
from app.services.bots.take_profit import merge_tp_config, resolve_take_profit  # noqa: E402


def _reset_db():
    if os.path.exists(db_conn.DB_PATH):
        try:
            os.remove(db_conn.DB_PATH)
        except PermissionError:
            conn = get_connection()
            cursor = conn.cursor()
            for table in (
                "bot_pending_fills",
                "bot_positions",
                "bot_trades",
                "bot_snapshots",
                "bot_logs",
                "orders",
                "positions",
                "bots",
            ):
                cursor.execute(f"DELETE FROM {table}")
            conn.commit()
            conn.close()
    else:
        init_db()
        return
    init_db()


def _insert_bot(bot_id: str, symbol: str = "BTCUSDT") -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (bot_id, "MACD_RSI", symbol, "1m", "RUNNING", 10000, "{}"),
    )
    conn.commit()
    conn.close()


class VirtualTpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _reset_db()

    def setUp(self):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bot_positions")
        conn.commit()
        conn.close()

    def test_apply_fill_persists_bot_risk(self):
        bot_id = str(uuid.uuid4())
        _insert_bot(bot_id)
        risk = {
            "stop_loss_percent": 2.0,
            "take_profit_percent": 5.0,
            "take_profit_price": None,
        }
        bot_positions.apply_fill(bot_id, "BTCUSDT", "BUY", 1.0, 100.0, risk=risk)
        pos = bot_positions.get_bot_position(bot_id, "BTCUSDT")
        self.assertAlmostEqual(pos["size"], 1.0)
        self.assertAlmostEqual(pos["take_profit_percent"], 5.0)
        self.assertAlmostEqual(pos["take_profit_price"], 105.0)

    def test_evaluate_risk_trigger_tp_only_for_one_slice(self):
        _insert_bot("bot-a", "ETHUSDT")
        _insert_bot("bot-b", "ETHUSDT")
        bot_positions.apply_fill(
            "bot-a",
            "ETHUSDT",
            "BUY",
            1.0,
            100.0,
            risk={"take_profit_percent": 2.0},
        )
        bot_positions.apply_fill(
            "bot-b",
            "ETHUSDT",
            "BUY",
            1.0,
            100.0,
            risk={"take_profit_percent": 10.0},
        )
        pos_a = bot_positions.get_bot_position("bot-a", "ETHUSDT")
        pos_b = bot_positions.get_bot_position("bot-b", "ETHUSDT")

        trigger_a, _ = bot_positions.evaluate_risk_trigger(
            pos_a["size"],
            pos_a["avg_price"],
            102.5,
            stop_loss_percent=pos_a["stop_loss_percent"],
            take_profit_percent=pos_a["take_profit_percent"],
            stop_loss_price=pos_a["stop_loss_price"],
            take_profit_price=pos_a["take_profit_price"],
        )
        trigger_b, _ = bot_positions.evaluate_risk_trigger(
            pos_b["size"],
            pos_b["avg_price"],
            102.5,
            stop_loss_percent=pos_b["stop_loss_percent"],
            take_profit_percent=pos_b["take_profit_percent"],
            stop_loss_price=pos_b["stop_loss_price"],
            take_profit_price=pos_b["take_profit_price"],
        )
        self.assertEqual(trigger_a, "TP")
        self.assertIsNone(trigger_b)

    def test_update_bot_risk_recomputes_prices(self):
        bot_id = str(uuid.uuid4())
        _insert_bot(bot_id, "AAPL")
        bot_positions.apply_fill(bot_id, "AAPL", "BUY", 2.0, 150.0)
        bot_positions.update_bot_risk(
            bot_id,
            "AAPL",
            150.0,
            "BUY",
            stop_loss_percent=1.0,
            take_profit_percent=4.0,
        )
        pos = bot_positions.get_bot_position(bot_id, "AAPL")
        self.assertAlmostEqual(pos["take_profit_price"], 156.0)
        self.assertAlmostEqual(pos["stop_loss_price"], 148.5)


class ParquetAppendTests(unittest.TestCase):
    def test_append_bars_parquet_writes_partitioned_file(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow not installed")

        out_dir = os.path.join(_TEST_DIR, "parquet_out")
        prev = app_config.ARCHIVE_PARQUET_DIR
        app_config.ARCHIVE_PARQUET_DIR = out_dir
        try:
            from app.services.archive.parquet_export import append_bars_parquet

            rows = [
                {
                    "symbol": "BTCUSDT",
                    "time": 1_700_000_000,
                    "open": 1.0,
                    "high": 2.0,
                    "low": 0.5,
                    "close": 1.5,
                    "volume": 10.0,
                    "source": "test",
                }
            ]
            written = append_bars_parquet(rows)
            self.assertEqual(written, 1)
            sym_dir = os.path.join(out_dir, "BTCUSDT")
            self.assertTrue(os.path.isdir(sym_dir))
            files = [f for f in os.listdir(sym_dir) if f.endswith(".parquet")]
            self.assertEqual(len(files), 1)
        finally:
            app_config.ARCHIVE_PARQUET_DIR = prev


class TakeProfitConfigTests(unittest.TestCase):
    def test_resolve_take_profit_from_merged_config(self):
        cfg = merge_tp_config("MACD_RSI", {"take_profit_percent": 7.5})
        tp_pct, tp_price = resolve_take_profit(cfg, {}, "BUY", 200.0)
        self.assertAlmostEqual(tp_pct, 7.5)
        self.assertAlmostEqual(tp_price, 215.0)


class BotConfigUpdateTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        _reset_db()

    def setUp(self):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bot_positions")
        cursor.execute("DELETE FROM bots")
        conn.commit()
        conn.close()

        self.bot_id = str(uuid.uuid4())
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                self.bot_id,
                "MACD_RSI",
                "BTCUSDT",
                "1m",
                "RUNNING",
                10000,
                '{"take_profit_percent": 2.0}',
            ),
        )
        conn.commit()
        conn.close()

        self.oms = MagicMock()
        self.oms.update_position_sl_tp = AsyncMock(return_value={"status": "success"})
        from app.services.bots.manager import BotManagerService

        self.manager = BotManagerService(self.oms, MagicMock(), MagicMock())

    async def test_update_bot_config_reapplies_open_slice_tp(self):
        bot_positions.apply_fill(
            self.bot_id,
            "BTCUSDT",
            "BUY",
            1.0,
            100.0,
            risk={"take_profit_percent": 2.0},
        )
        detail = await self.manager.update_bot_config(
            self.bot_id,
            {"take_profit_percent": 6.0},
        )
        self.assertAlmostEqual(detail["bot"]["config"]["take_profit_percent"], 6.0)
        pos = bot_positions.get_bot_position(self.bot_id, "BTCUSDT")
        self.assertAlmostEqual(pos["take_profit_price"], 106.0)


if __name__ == "__main__":
    unittest.main()
