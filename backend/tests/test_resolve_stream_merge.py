"""Phase 2 — streaming broker pages fold into resolve merge without a full remote list."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ.setdefault("ARCHIVE_ENABLED", "false")
os.environ["DATABASE_URL"] = ""

_TEST_DIR = tempfile.mkdtemp()
os.environ["SQLITE_DB_PATH"] = os.path.join(_TEST_DIR, "resolve_stream_merge.db")

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.environ["SQLITE_DB_PATH"]
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH
app_config.ARCHIVE_ENABLED = False

from app.services.archive.resolve import (  # noqa: E402
    _broker_fill_candles,
    fold_candles_into,
    materialize_candle_window,
    merge_candle_series,
)


def _bar(t: int, close: float) -> dict:
    return {
        "time": t,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1.0,
    }


class TestResolveStreamMerge(unittest.TestCase):
    def test_fold_pages_match_bulk_merge(self) -> None:
        remote_a = [_bar(1_000_000 + i * 60, 10.0 + i) for i in range(5)]
        remote_b = [_bar(1_000_000 + (5 + i) * 60, 20.0 + i) for i in range(4)]
        local = [_bar(1_000_000 + 3 * 60, 99.0)]  # overlaps remote_a[3]

        bulk = merge_candle_series(remote_a + remote_b, local, align_secs=60)

        by_time: dict[int, dict] = {}
        fold_candles_into(by_time, remote_a, align_secs=60)
        fold_candles_into(by_time, remote_b, align_secs=60)
        fold_candles_into(by_time, local, align_secs=60)
        streamed = materialize_candle_window(by_time)

        self.assertEqual(streamed, bulk)
        self.assertEqual(streamed[3]["close"], 99.0)

    def test_broker_fill_folds_pages_without_list_api(self) -> None:
        from_ts = (1_700_000_000 // 60) * 60
        to_ts = from_ts + 10 * 86400
        local = [_bar(to_ts - i * 60, 50.0) for i in range(3 * 24 * 60)]
        local.reverse()

        page1 = [_bar(from_ts + i * 60, 40.0) for i in range(2 * 24 * 60)]
        page2 = [
            _bar(from_ts + (2 * 24 * 60 + i) * 60, 41.0)
            for i in range(5 * 24 * 60)
        ]
        calls = {"n": 0}

        def fake_pages(symbol, f, t, timeframe, **kwargs):
            calls["n"] += 1
            yield page1
            yield page2

        with patch(
            "app.services.archive.broker_fetch.iter_broker_tf_candle_pages",
            side_effect=fake_pages,
        ), patch(
            "app.services.archive.broker_fetch.fetch_broker_tf_candles",
        ) as mock_list:
            filled, src = _broker_fill_candles(
                "BTCUSDT",
                local,
                from_ts=from_ts,
                to_ts=to_ts,
                timeframe="1m",
            )

        mock_list.assert_not_called()
        self.assertEqual(src, "broker REST 1m")
        self.assertGreaterEqual(len(filled), len(local))
        self.assertEqual(filled[0]["time"], from_ts)
        self.assertEqual(filled[-1]["time"], local[-1]["time"])
        # Local wins on the overlapping recent tail.
        overlap_t = local[0]["time"]
        hit = next(b for b in filled if b["time"] == overlap_t)
        self.assertEqual(hit["close"], 50.0)
        self.assertGreaterEqual(calls["n"], 1)

    def test_broker_fill_empty_pages_returns_local(self) -> None:
        from_ts = 1_700_000_000
        to_ts = from_ts + 20 * 86400
        # Short local — coverage not OK for 20d
        local = [_bar(to_ts - i * 60, 1.0) for i in range(100)]
        local.reverse()

        with patch(
            "app.services.archive.broker_fetch.iter_broker_tf_candle_pages",
            side_effect=lambda *a, **k: iter([]),
        ):
            filled, src = _broker_fill_candles(
                "AAPL",
                local,
                from_ts=from_ts,
                to_ts=to_ts,
                timeframe="1m",
            )

        self.assertIsNone(src)
        self.assertIs(filled, local)


if __name__ == "__main__":
    unittest.main()
