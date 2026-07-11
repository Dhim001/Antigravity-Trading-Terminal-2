"""Page-wise Massive REST aggs → candles (Phase 1 memory path)."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""

_TEST_DIR = tempfile.mkdtemp()
os.environ["SQLITE_DB_PATH"] = os.path.join(_TEST_DIR, "broker_fetch_test.db")

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.environ["SQLITE_DB_PATH"]
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH
app_config.MASSIVE_API_KEY = "test-key"
app_config.MASSIVE_REST_URL = "https://api.polygon.io"

from app.services.archive import broker_fetch as bf  # noqa: E402
from app.services.massive_bars import aggs_to_candles, aggs_to_candles_native  # noqa: E402


def _agg(t_ms: int, close: float = 1.0) -> dict:
    return {"t": t_ms, "o": close, "h": close, "l": close, "c": close, "v": 10}


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class TestMassivePageStream(unittest.TestCase):
    def test_iter_pages_yields_without_requiring_full_list(self) -> None:
        page1 = [_agg(1_704_110_400_000 + i * 60_000, float(i)) for i in range(3)]
        page2 = [_agg(1_704_110_580_000 + i * 60_000, float(10 + i)) for i in range(2)]
        payloads = [
            {
                "results": page1,
                "next_url": "https://api.polygon.io/v2/aggs/ticker/X/range/1/minute/a/b?cursor=2",
            },
            {"results": page2},
        ]
        call = {"n": 0}

        def fake_get(url, params=None):
            idx = call["n"]
            call["n"] += 1
            return _FakeResponse(payloads[idx])

        client = MagicMock()
        client.get.side_effect = fake_get
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        with patch.object(bf, "MASSIVE_API_KEY", "test-key"), patch.object(
            bf, "httpx"
        ) as httpx_mod:
            httpx_mod.Client.return_value = client
            pages = list(
                bf._iter_massive_agg_pages(
                    "BTCUSDT",
                    1_704_110_400,
                    1_704_120_000,
                    multiplier=1,
                    timespan="minute",
                )
            )

        self.assertEqual(len(pages), 2)
        self.assertEqual(len(pages[0]), 3)
        self.assertEqual(len(pages[1]), 2)
        self.assertEqual(call["n"], 2)

    def test_fetch_tf_candles_matches_bulk_convert(self) -> None:
        page1 = [_agg(1_704_110_400_000 + i * 60_000, float(i + 1)) for i in range(4)]
        page2 = [_agg(1_704_110_640_000 + i * 60_000, float(20 + i)) for i in range(3)]
        all_aggs = page1 + page2
        payloads = [
            {
                "results": page1,
                "next_url": "https://api.polygon.io/v2/aggs/ticker/X/range/1/minute/a/b?cursor=2",
            },
            {"results": page2},
        ]
        call = {"n": 0}

        def fake_get(url, params=None):
            idx = call["n"]
            call["n"] += 1
            return _FakeResponse(payloads[idx])

        client = MagicMock()
        client.get.side_effect = fake_get
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        from_ts = 1_704_110_400
        to_ts = 1_704_120_000
        expected = [
            c for c in aggs_to_candles(all_aggs) if from_ts <= int(c["time"]) <= to_ts
        ]

        with patch.object(bf, "MASSIVE_API_KEY", "test-key"), patch.object(
            bf, "httpx"
        ) as httpx_mod:
            httpx_mod.Client.return_value = client
            got = bf.fetch_massive_tf_candles(
                "BTCUSDT", from_ts, to_ts, "1m", symbol_info={"type": "crypto"}
            )

        self.assertEqual(len(got), len(expected))
        self.assertEqual([c["time"] for c in got], [c["time"] for c in expected])
        self.assertEqual([c["close"] for c in got], [c["close"] for c in expected])

    def test_fetch_tf_candles_native_1h_parity(self) -> None:
        # Hourly bars: t spaced by 1h
        page = [
            _agg(1_704_110_400_000 + i * 3_600_000, float(100 + i)) for i in range(5)
        ]
        payloads = [{"results": page}]
        call = {"n": 0}

        def fake_get(url, params=None):
            idx = call["n"]
            call["n"] += 1
            return _FakeResponse(payloads[idx])

        client = MagicMock()
        client.get.side_effect = fake_get
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        from_ts = 1_704_110_400
        to_ts = 1_704_130_000
        expected = [
            c
            for c in aggs_to_candles_native(page)
            if from_ts <= int(c["time"]) <= to_ts
        ]

        with patch.object(bf, "MASSIVE_API_KEY", "test-key"), patch.object(
            bf, "httpx"
        ) as httpx_mod:
            httpx_mod.Client.return_value = client
            got = bf.fetch_massive_tf_candles(
                "BTCUSDT", from_ts, to_ts, "1h", symbol_info={"type": "crypto"}
            )

        self.assertEqual([c["close"] for c in got], [c["close"] for c in expected])

    def test_empty_when_no_api_key(self) -> None:
        with patch.object(bf, "MASSIVE_API_KEY", ""):
            self.assertEqual(
                list(
                    bf._iter_massive_agg_pages(
                        "BTCUSDT", 1, 100, multiplier=1, timespan="minute"
                    )
                ),
                [],
            )
            self.assertEqual(bf.fetch_massive_tf_candles("BTCUSDT", 1, 100, "1m"), [])

    def test_massive_miss_skips_second_massive_fetch(self) -> None:
        """After Massive yields nothing, fallback must not re-call Massive."""
        with patch.object(bf, "MASSIVE_API_KEY", "test-key"), patch.object(
            bf, "iter_massive_tf_candle_pages", return_value=iter([])
        ), patch.object(bf, "fetch_massive_1m_bars") as massive_1m, patch.object(
            bf, "fetch_binance_1m_bars", return_value=[]
        ), patch.object(bf, "fetch_alpaca_1m_bars", return_value=[]):
            pages = list(
                bf.iter_broker_tf_candle_pages("AAPL", 1_700_000_000, 1_700_100_000, "1m")
            )
            self.assertEqual(pages, [])
            massive_1m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
