"""Tests for live bot candle sourcing."""

import os
import tempfile
import time
import unittest

_TEST_DIR = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = ""
os.environ["ARCHIVE_ENABLED"] = "true"
os.environ["BOT_MIN_CANDLES"] = "200"

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "bot_candles_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH
app_config.BOT_MIN_CANDLES = 200

from app.database import init_db  # noqa: E402
from app.services.archive.writer import _upsert_1m_rows  # noqa: E402
from app.services.bots.candle_source import get_bot_candles  # noqa: E402
from app.services.bots.strategies import normalize_strategy_name  # noqa: E402


class _StubFeed:
    def __init__(self, candles):
        self._candles = candles

    def get_candles(self, symbol):
        return self._candles.get(symbol, [])


class BotCandleSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_returns_live_when_sufficient(self):
        live = [{"time": 1000 + i * 60, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10} for i in range(250)]
        feed = _StubFeed({"AAPL": live})
        out = get_bot_candles("AAPL", feed, min_bars=200)
        self.assertEqual(len(out), 250)
        self.assertIs(out, live)

    def test_merges_archive_when_live_buffer_short(self):
        symbol = "BTCUSDT"
        now = int(time.time())
        now = (now // 60) * 60
        start = now - 300 * 60
        rows = []
        for i in range(300):
            t = start + i * 60
            price = 100.0 + i * 0.01
            rows.append({
                "symbol": symbol,
                "time": t,
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price + 0.5,
                "volume": 1.0,
                "source": "test",
            })
        _upsert_1m_rows(rows)

        live_tail = rows[-50:]
        live = [
            {
                "time": r["time"],
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"],
            }
            for r in live_tail
        ]
        feed = _StubFeed({symbol: live})
        out = get_bot_candles(symbol, feed, min_bars=200)
        self.assertGreaterEqual(len(out), 200)
        merged_times = {b["time"] for b in out}
        self.assertIn(live[-1]["time"], merged_times)

    def test_normalize_strategy_aliases(self):
        self.assertEqual(normalize_strategy_name("supertrend"), "SUPERTREND_ADX")
        self.assertEqual(normalize_strategy_name("MACD_RSI"), "MACD_RSI")


if __name__ == "__main__":
    unittest.main()
