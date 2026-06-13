"""Tests for long-term market bar archive."""

import os
import tempfile
import time
import unittest

# Isolate archive tests to a temp SQLite DB
_TEST_DIR = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = ""
os.environ["ARCHIVE_ENABLED"] = "true"
os.environ["ARCHIVE_RETENTION_1M_DAYS"] = "90"
os.environ["ARCHIVE_RETENTION_1H_DAYS"] = "1825"

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "archive_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH

from app.database import init_db  # noqa: E402
from app.services.archive.writer import ArchiveWriter, _upsert_1m_rows, get_archive_writer  # noqa: E402
from app.services.archive.rollup import rollup_symbol, run_rollup_job, _aggregate_hour  # noqa: E402
from app.services.archive.query import query_market_history, query_1m, query_1h  # noqa: E402


def _seed_1m(symbol: str, start: int, count: int, base: float = 100.0) -> None:
    rows = []
    for i in range(count):
        t = start + i * 60
        price = base + i * 0.1
        rows.append({
            "symbol": symbol,
            "time": t,
            "open": price,
            "high": price + 0.5,
            "low": price - 0.5,
            "close": price + 0.2,
            "volume": 10 + i,
            "source": "SIMULATED",
        })
    _upsert_1m_rows(rows)


class TestArchiveWriter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        conn = db_conn.get_connection()
        conn.cursor().execute("DELETE FROM market_bars_1m")
        conn.cursor().execute("DELETE FROM market_bars_1h")
        conn.commit()
        conn.close()
        import app.services.archive.writer as wmod
        wmod._writer = ArchiveWriter()

    def test_upsert_and_dedupe_same_minute(self):
        writer = get_archive_writer()
        bar = {"time": 1718006400, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100}
        writer.record_bar("BTCUSDT", bar, "SIMULATED")
        bar["close"] = 1.8
        writer.record_bar("BTCUSDT", bar, "SIMULATED")
        flushed = writer.flush()
        self.assertEqual(flushed, 1)
        bars = query_1m("BTCUSDT", 1718006400, 1718006400)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["close"], 1.8)


class TestArchiveRollup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        conn = db_conn.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM market_bars_1m")
        cur.execute("DELETE FROM market_bars_1h")
        conn.commit()
        conn.close()

    def test_aggregate_hour_math(self):
        hour = 1718006400
        bars = [
            {"symbol": "AAPL", "time": hour + i * 60, "open": 100 + i, "high": 101 + i,
             "low": 99 + i, "close": 100.5 + i, "volume": 10, "source": "SIMULATED"}
            for i in range(3)
        ]
        agg = _aggregate_hour(bars)
        self.assertEqual(agg["time"], hour)
        self.assertEqual(agg["open"], 100)
        self.assertEqual(agg["close"], 102.5)
        self.assertEqual(agg["high"], 103)
        self.assertEqual(agg["low"], 99)
        self.assertEqual(agg["volume"], 30)
        self.assertEqual(agg["bar_count"], 3)

    def test_rollup_moves_old_1m_to_1h(self):
        old_hour = int(time.time()) - (100 * 86400)
        old_hour = (old_hour // 3600) * 3600
        _seed_1m("AAPL", old_hour, 60, base=50.0)

        cutoff = int(time.time()) - (90 * 86400)
        hours, deleted = rollup_symbol("AAPL", cutoff)
        self.assertGreater(hours, 0)
        self.assertEqual(deleted, 60)

        remaining = query_1m("AAPL", old_hour, old_hour + 3600)
        self.assertEqual(len(remaining), 0)

        hourly = query_1h("AAPL", old_hour, old_hour)
        self.assertEqual(len(hourly), 1)
        self.assertEqual(hourly[0]["open"], 50.0)


class TestArchiveQuery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        conn = db_conn.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM market_bars_1m")
        cur.execute("DELETE FROM market_bars_1h")
        conn.commit()
        conn.close()

    def test_query_auto_interval(self):
        now = int(time.time())
        recent = (now // 60) * 60 - 3600
        _seed_1m("BTCUSDT", recent, 5, base=200.0)

        old_hour = now - (100 * 86400)
        old_hour = (old_hour // 3600) * 3600
        _upsert_1m_rows([{
            "symbol": "BTCUSDT",
            "time": old_hour,
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1.5,
            "volume": 5,
            "source": "SIMULATED",
        }])
        rollup_symbol("BTCUSDT", now - (90 * 86400))

        bars = query_market_history(
            "BTCUSDT",
            from_ts=old_hour,
            to_ts=recent + 240,
            interval="auto",
        )
        self.assertGreaterEqual(len(bars), 2)


class TestArchiveResolve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        conn = db_conn.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM market_bars_1m")
        cur.execute("DELETE FROM market_bars_1h")
        conn.commit()
        conn.close()

    def test_resolve_merges_archive_and_live(self):
        from app.services.archive.resolve import resolve_candles_for_range

        now = int(time.time())
        archived_start = now - 86400 * 3
        _seed_1m("ETHUSDT", archived_start, 100, base=3000.0)

        class FakeFeed:
            def get_candles(self, symbol):
                live_start = now - 3600
                return [
                    {
                        "time": live_start + i * 60,
                        "open": 3100 + i,
                        "high": 3101 + i,
                        "low": 3099 + i,
                        "close": 3100.5 + i,
                        "volume": 5,
                    }
                    for i in range(60)
                ]

        candles, meta = resolve_candles_for_range(
            "ETHUSDT",
            FakeFeed(),
            from_ts=archived_start,
            to_ts=now,
            interval="1m",
        )
        self.assertGreater(meta["archived_bars"], 0)
        self.assertGreater(meta["live_bars"], 0)
        self.assertGreater(len(candles), 100)
        self.assertLessEqual(candles[0]["time"], archived_start + 60)
        self.assertGreaterEqual(candles[-1]["time"], now - 7200)


class TestArchiveBackfill(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        conn = db_conn.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM market_bars_1m WHERE symbol = 'TESTSYM'")
        conn.commit()
        conn.close()

    def test_backfill_from_feed_buffer(self):
        from app.services.archive.backfill import backfill_symbol_from_feed, symbol_has_archive

        class FakeFeed:
            def get_candles(self, symbol):
                base = 1718006400
                return [
                    {
                        "time": base + i * 60,
                        "open": 100 + i,
                        "high": 101 + i,
                        "low": 99 + i,
                        "close": 100.5 + i,
                        "volume": 10,
                    }
                    for i in range(10)
                ]

        self.assertFalse(symbol_has_archive("TESTSYM"))
        n = backfill_symbol_from_feed(FakeFeed(), "TESTSYM", "SIMULATED")
        self.assertEqual(n, 10)
        self.assertTrue(symbol_has_archive("TESTSYM"))


if __name__ == "__main__":
    unittest.main()
