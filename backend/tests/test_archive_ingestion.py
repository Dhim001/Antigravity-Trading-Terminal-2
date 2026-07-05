"""Tests for archive gap scan and broker ingestion orchestration."""

import os
import tempfile
import time
import unittest
from unittest.mock import patch

_TEST_DIR = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = ""
os.environ["ARCHIVE_ENABLED"] = "true"
os.environ["ARCHIVE_INGESTION_DAYS"] = "90"
os.environ["ARCHIVE_RETENTION_1M_DAYS"] = "90"
os.environ["MASSIVE_API_KEY"] = ""

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "ingest_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH
app_config.MASSIVE_API_KEY = ""

from app.database import init_db  # noqa: E402
from app.services.archive.gap_scan import (
    filter_unknown_gaps,
    find_gap_ranges,
    get_symbol_bar_span,
    is_gap_range_known,
    record_known_gap_range,
)  # noqa: E402
from app.services.archive.ingestion import ingest_symbol_backfill, run_archive_ingestion  # noqa: E402
from app.services.archive.writer import _upsert_1m_rows  # noqa: E402


def _seed_bars(symbol: str, start: int, count: int, *, step: int = 60, skip_at: int | None = None) -> None:
    rows = []
    t = start
    written = 0
    i = 0
    while written < count:
        if skip_at is not None and i == skip_at:
            t += step * 5
            i += 1
            continue
        price = 100.0 + written
        rows.append({
            "symbol": symbol,
            "time": t,
            "open": price,
            "high": price + 1,
            "low": price - 1,
            "close": price,
            "volume": 10.0,
            "source": "TEST",
        })
        t += step
        written += 1
        i += 1
    _upsert_1m_rows(rows)


class ArchiveGapScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        conn = db_conn.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM market_bars_1m")
        cur.execute("DELETE FROM archive_ingestion_state")
        cur.execute("DELETE FROM archive_known_gaps")
        conn.commit()
        conn.close()

    def test_find_gap_ranges_detects_missing_minutes(self):
        base = int(time.time()) - 3600
        _seed_bars("AAPL", base, 30, skip_at=10)
        gaps = find_gap_ranges("AAPL", from_ts=base, to_ts=base + 3600, gap_threshold_sec=120)
        self.assertEqual(len(gaps), 1)
        self.assertGreater(gaps[0][1], gaps[0][0])

    def test_get_symbol_bar_span(self):
        base = int(time.time()) - 600
        _seed_bars("MSFT", base, 10)
        span = get_symbol_bar_span("MSFT")
        self.assertEqual(span["count"], 10)
        self.assertEqual(span["oldest"], base)
        self.assertEqual(span["newest"], base + 9 * 60)


class ArchiveIngestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        conn = db_conn.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM market_bars_1m")
        cur.execute("DELETE FROM archive_ingestion_state")
        cur.execute("DELETE FROM archive_known_gaps")
        conn.commit()
        conn.close()

    @patch("app.services.archive.ingestion.fetch_broker_1m_bars")
    def test_ingest_symbol_backfill_calls_broker(self, mock_fetch):
        now = int(time.time())
        mock_fetch.return_value = [{
            "symbol": "AAPL",
            "time": now - 120,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 100.0,
            "source": "MASSIVE_REST",
        }]
        result = ingest_symbol_backfill("AAPL", days=7)
        self.assertGreater(result["broker"], 0)
        self.assertGreater(result["rows_written"], 0)
        span = get_symbol_bar_span("AAPL")
        self.assertEqual(span["count"], 1)

    @patch("app.services.archive.ingestion.ingest_symbol_backfill")
    def test_run_archive_ingestion_iterates_symbols(self, mock_ingest):
        mock_ingest.return_value = {"rows_written": 2, "broker": 2}
        result = run_archive_ingestion(["AAPL", "MSFT"], include_seed_backfill=False)
        self.assertEqual(result["symbols"], 2)
        self.assertEqual(mock_ingest.call_count, 2)

    @patch("app.services.archive.ingestion.ingest_symbol_backfill")
    def test_run_archive_ingestion_startup_batch(self, mock_ingest):
        mock_ingest.return_value = {"rows_written": 1}
        syms = ["A", "B", "C", "D", "E", "F", "G", "H"]
        result = run_archive_ingestion(syms, include_seed_backfill=False, max_symbols=3)
        self.assertEqual(result["symbols"], 3)
        self.assertEqual(result["symbols_deferred"], 5)
        self.assertEqual(mock_ingest.call_count, 3)


class ArchiveKnownGapsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        conn = db_conn.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM archive_known_gaps")
        conn.commit()
        conn.close()

    def test_known_gap_range_skipped(self):
        record_known_gap_range("AAPL", 1000, 2000, reason="no_bars")
        self.assertTrue(is_gap_range_known("AAPL", 1000, 2000))
        gaps = filter_unknown_gaps("AAPL", [(1000, 2000), (3000, 4000)])
        self.assertEqual(gaps, [(3000, 4000)])


class ArchiveBrokerSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        conn = db_conn.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM market_bars_1m")
        cur.execute("DELETE FROM archive_known_gaps")
        conn.commit()
        conn.close()

    def test_resolve_alpaca_when_live_alpaca(self):
        import app.services.archive.broker_fetch as bf
        with patch.object(bf, "TERMINAL_MODE", "LIVE_ALPACA"), patch.object(bf, "MASSIVE_API_KEY", ""), patch.object(bf, "ALPACA_API_KEY", "k"), patch.object(bf, "ALPACA_SECRET_KEY", "s"):
            self.assertEqual(bf.resolve_broker_source(), "alpaca")

    @patch("app.services.archive.broker_fetch.httpx.Client")
    def test_fetch_alpaca_parses_bars(self, mock_client_cls):
        import app.services.archive.broker_fetch as bf
        bar_time = "2024-01-01T15:00:00Z"
        bar_ts = bf._parse_alpaca_bar_time(bar_time)
        mock_client = mock_client_cls.return_value.__enter__.return_value
        mock_client.get.return_value = type("R", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "bars": {"AAPL": [{"t": bar_time, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}]},
                "next_page_token": None,
            },
        })()
        with patch.object(bf, "ALPACA_API_KEY", "k"), patch.object(bf, "ALPACA_SECRET_KEY", "s"):
            rows = bf.fetch_alpaca_1m_bars("AAPL", bar_ts - 60, bar_ts + 60)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "AAPL")
        self.assertEqual(rows[0]["source"], "ALPACA_REST")

    @patch("app.services.archive.ingestion.ingest_broker_range")
    def test_ingest_symbol_gaps_records_unfillable(self, mock_range):
        base = int(time.time()) - 3600
        _seed_bars("AAPL", base, 30, skip_at=10)
        mock_range.return_value = 0
        gaps = find_gap_ranges("AAPL", from_ts=base, to_ts=base + 3600)
        self.assertGreater(len(gaps), 0)
        import app.services.archive.ingestion as ing
        with patch.object(ing, "resolve_broker_source", return_value="massive"):
            rows = ing.ingest_symbol_gaps("AAPL", days=1)
        self.assertEqual(rows, 0)
        self.assertTrue(is_gap_range_known("AAPL", gaps[0][0], gaps[0][1]))


if __name__ == "__main__":
    unittest.main()
