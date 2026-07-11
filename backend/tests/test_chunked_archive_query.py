"""Phase 3 — chunked SQLite archive reads + capped footprint aggregation."""

from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""
os.environ["ARCHIVE_ENABLED"] = "true"
os.environ["ARCHIVE_TICKS_ENABLED"] = "true"

_TEST_DIR = tempfile.mkdtemp()
os.environ["SQLITE_DB_PATH"] = os.path.join(_TEST_DIR, "chunked_query_test.db")

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.environ["SQLITE_DB_PATH"]
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH
app_config.ARCHIVE_QUERY_BATCH_SIZE = 3
app_config.ARCHIVE_QUERY_LIMIT = 50000

from app.database import init_db  # noqa: E402
from app.services.archive.query import (  # noqa: E402
    iter_table_bars,
    query_1m,
    query_footprint,
    query_footprint_detailed,
)
from app.services.archive.writer import _upsert_1m_rows  # noqa: E402


def _seed_ticks(symbol: str, start_ms: int, n: int, *, price: float = 100.0, step_ms: int = 1000) -> None:
    conn = db_conn.get_connection()
    cur = conn.cursor()
    for i in range(n):
        cur.execute(
            """
            INSERT OR REPLACE INTO market_ticks (
                symbol, time_ms, price, volume, source, bid, ask, spread, tick_type
            ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, 'trade')
            """,
            (symbol, start_ms + i * step_ms, price + (i % 5), 1.0 + i * 0.1, "TEST"),
        )
    conn.commit()
    conn.close()


def _seed_1m(symbol: str, start: int, count: int) -> None:
    rows = [
        {
            "symbol": symbol,
            "time": start + i * 60,
            "open": 10.0 + i,
            "high": 11.0 + i,
            "low": 9.0 + i,
            "close": 10.5 + i,
            "volume": float(i + 1),
            "source": "TEST",
        }
        for i in range(count)
    ]
    _upsert_1m_rows(rows)


class TestChunkedArchiveQuery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        conn = db_conn.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM market_bars_1m")
        cur.execute("DELETE FROM market_ticks")
        conn.commit()
        conn.close()

    def test_iter_table_bars_matches_list_api(self):
        start = 1_700_000_000
        _seed_1m("ETHUSDT", start, 20)
        listed = query_1m("ETHUSDT", start, start + 20 * 60)
        streamed = list(
            iter_table_bars(
                "market_bars_1m",
                "ETHUSDT",
                start,
                start + 20 * 60,
                batch_size=3,
            )
        )
        self.assertEqual(len(streamed), 20)
        self.assertEqual(streamed, listed)

    def test_footprint_chunk_parity_with_single_window(self):
        start_ms = 1_700_000_000_000
        _seed_ticks("BTCUSDT", start_ms, 120, step_ms=30_000)  # 1 hour of ticks
        to_ms = start_ms + 119 * 30_000
        price_step = 1.0
        bucket = 60_000

        # Force many small chunks vs one large chunk — same cells under cap.
        cells_chunked, meta_c = query_footprint_detailed(
            "BTCUSDT",
            start_ms,
            to_ms,
            price_step,
            bucket,
            max_range_ms=24 * 3600 * 1000,
            chunk_ms=5 * 60 * 1000,
            max_cells=50_000,
        )
        cells_one, meta_o = query_footprint_detailed(
            "BTCUSDT",
            start_ms,
            to_ms,
            price_step,
            bucket,
            max_range_ms=24 * 3600 * 1000,
            chunk_ms=24 * 3600 * 1000,
            max_cells=50_000,
        )
        self.assertGreater(meta_c["chunks"], 1)
        self.assertEqual(meta_o["chunks"], 1)
        self.assertEqual(cells_chunked, cells_one)
        self.assertFalse(meta_c["truncated"])
        self.assertEqual(query_footprint("BTCUSDT", start_ms, to_ms, price_step, bucket), cells_chunked)

    def test_footprint_clamps_wide_range(self):
        start_ms = 1_700_000_000_000
        _seed_ticks("BTCUSDT", start_ms, 10, step_ms=60_000)
        cells, meta = query_footprint_detailed(
            "BTCUSDT",
            start_ms,
            start_ms + 48 * 3600 * 1000,
            1.0,
            60_000,
            max_range_ms=3600_000,
            chunk_ms=600_000,
            max_cells=50_000,
        )
        self.assertTrue(meta["clamped"])
        self.assertEqual(meta["to_ts"] - meta["from_ts"], 3600_000)
        self.assertIsInstance(cells, list)

    def test_footprint_cell_cap_truncates(self):
        start_ms = 1_700_000_000_000
        # Distinct prices → many cells
        conn = db_conn.get_connection()
        cur = conn.cursor()
        for i in range(40):
            cur.execute(
                """
                INSERT OR REPLACE INTO market_ticks (
                    symbol, time_ms, price, volume, source, bid, ask, spread, tick_type
                ) VALUES (?, ?, ?, 1.0, 'TEST', NULL, NULL, NULL, 'trade')
                """,
                ("SOLUSDT", start_ms + i * 60_000, 100.0 + i * 10.0),
            )
        conn.commit()
        conn.close()

        cells, meta = query_footprint_detailed(
            "SOLUSDT",
            start_ms,
            start_ms + 40 * 60_000,
            price_step=10.0,
            time_bucket_ms=60_000,
            max_range_ms=24 * 3600 * 1000,
            chunk_ms=60_000,
            max_cells=10,
        )
        self.assertTrue(meta["truncated"])
        self.assertLessEqual(len(cells), 10)
        self.assertEqual(meta["cell_count"], len(cells))

    def test_ui_purpose_uses_lower_limit(self):
        from app.services.archive.query import archive_query_limit, query_1m

        start = 1_700_000_000
        _seed_1m("ADAUSDT", start, 30)
        prev_ui = app_config.ARCHIVE_QUERY_LIMIT_UI
        prev_default = app_config.ARCHIVE_QUERY_LIMIT

        try:
            app_config.ARCHIVE_QUERY_LIMIT_UI = 5
            app_config.ARCHIVE_QUERY_LIMIT = 50
            self.assertEqual(archive_query_limit("ui"), 5)
            self.assertEqual(archive_query_limit("default"), 50)
            ui_bars = query_1m(
                "ADAUSDT", start, start + 30 * 60, purpose="ui"
            )
            full = query_1m("ADAUSDT", start, start + 30 * 60)
            self.assertEqual(len(ui_bars), 5)
            self.assertEqual(len(full), 30)
            # Newest-N: last UI bar matches last full bar.
            self.assertEqual(ui_bars[-1]["time"], full[-1]["time"])
            self.assertGreater(ui_bars[0]["time"], full[0]["time"])
        finally:
            app_config.ARCHIVE_QUERY_LIMIT_UI = prev_ui
            app_config.ARCHIVE_QUERY_LIMIT = prev_default

    def test_newest_n_truncation_meta(self):
        from app.services.archive.query import query_market_history_detailed

        start = 1_700_000_000
        _seed_1m("XRPUSDT", start, 20)
        bars, meta = query_market_history_detailed(
            "XRPUSDT",
            from_ts=start,
            to_ts=start + 20 * 60,
            interval="1m",
            limit=5,
        )
        self.assertTrue(meta["truncated"])
        self.assertEqual(len(bars), 5)
        self.assertEqual(bars[-1]["time"], start + 19 * 60)


if __name__ == "__main__":
    unittest.main()
