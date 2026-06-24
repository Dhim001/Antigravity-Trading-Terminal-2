"""Tests for LIVE_MASSIVE broadcast diff helpers."""

from __future__ import annotations

import unittest

from app.server import _market_snapshot_changed


class TestMassiveBroadcastDiff(unittest.TestCase):
    def test_unchanged_snapshot(self) -> None:
        snap = {
            "symbol": "AAPL",
            "price": 100.0,
            "change_24h": 1.0,
            "volume_24h": 1000,
            "high_24h": 101,
            "low_24h": 99,
            "candle": {"time": 1, "open": 99, "high": 101, "low": 98, "close": 100, "volume": 10},
        }
        self.assertFalse(_market_snapshot_changed(snap, dict(snap)))

    def test_price_change_detected(self) -> None:
        prev = {"symbol": "AAPL", "price": 100.0, "candle": {}}
        cur = {"symbol": "AAPL", "price": 101.0, "candle": {}}
        self.assertTrue(_market_snapshot_changed(prev, cur))

    def test_empty_candle_both_sides(self) -> None:
        prev = {"symbol": "AAPL", "price": 100.0, "candle": {}}
        cur = {"symbol": "AAPL", "price": 100.0, "candle": {}}
        self.assertFalse(_market_snapshot_changed(prev, cur))


if __name__ == "__main__":
    unittest.main()
