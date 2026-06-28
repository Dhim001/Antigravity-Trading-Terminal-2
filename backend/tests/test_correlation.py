"""Unit tests for dynamic correlation monitoring."""

from __future__ import annotations

import math
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.bots.correlation import (  # noqa: E402
    align_return_series,
    build_correlation_matrix,
    cluster_correlated,
    daily_closes_from_bars,
    log_returns,
    pairwise_correlation_matrix,
    pearson,
    resolve_correlation_group,
    static_correlation_group,
    winsorize,
)


class PearsonTests(unittest.TestCase):
    def test_identical_series(self):
        self.assertAlmostEqual(pearson([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]), 1.0)

    def test_inverted_series(self):
        self.assertAlmostEqual(pearson([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]), -1.0)


class LogReturnTests(unittest.TestCase):
    def test_log_return_calculation(self):
        rets = log_returns({"2026-01-01": 100.0, "2026-01-02": 110.0})
        self.assertAlmostEqual(rets["2026-01-02"], math.log(1.1), places=6)


class ClusterTests(unittest.TestCase):
    def test_clusters_highly_correlated_symbols(self):
        symbols = ["A", "B", "C"]
        matrix = [
            [1.0, 0.9, 0.1],
            [0.9, 1.0, 0.2],
            [0.1, 0.2, 1.0],
        ]
        groups = cluster_correlated(symbols, matrix, 0.7)
        self.assertEqual(len(groups), 2)
        ab_group = next(m for m in groups.values() if "A" in m)
        self.assertIn("B", ab_group)
        self.assertNotIn("C", ab_group)


class AlignReturnsTests(unittest.TestCase):
    def test_aligns_common_days_log_returns(self):
        closes = {
            "AAPL": {"2026-01-01": 100.0, "2026-01-02": 101.0, "2026-01-03": 102.0},
            "MSFT": {"2026-01-01": 200.0, "2026-01-02": 202.0, "2026-01-03": 204.0},
        }
        symbols, series, days = align_return_series(closes, min_days=2, winsorize_pct=0)
        self.assertEqual(symbols, ["AAPL", "MSFT"])
        self.assertEqual(len(days), 2)
        self.assertAlmostEqual(series["AAPL"][0], math.log(101 / 100), places=6)


class PairwiseTests(unittest.TestCase):
    def test_no_zero_fill_on_sparse_days(self):
        day_series = {
            "A": {"2026-01-01": 0.01, "2026-01-02": 0.02, "2026-01-03": 0.01},
            "B": {"2026-01-02": 0.015, "2026-01-03": 0.012},
        }
        matrix, overlap = pairwise_correlation_matrix(["A", "B"], day_series, min_days=2)
        self.assertEqual(overlap, 2)
        self.assertAlmostEqual(matrix[0][1], pearson([0.02, 0.01], [0.015, 0.012]))


class EquitySessionTests(unittest.TestCase):
    def test_equity_bars_skip_weekends(self):
        # Monday 2026-01-05 21:00 UTC = 4pm ET
        mon_ts = 1767646800
        bars = [
            {"time": mon_ts, "close": 100.0},
            {"time": mon_ts + 86400, "close": 101.0},  # Tuesday
        ]
        daily = daily_closes_from_bars(bars, crypto=False)
        self.assertEqual(len(daily), 2)


class ResolveGroupTests(unittest.TestCase):
    @unittest.mock.patch("app.services.bots.correlation.RISK_DYNAMIC_CORRELATION_ENABLED", False)
    def test_static_fallback_when_dynamic_disabled(self):
        self.assertEqual(resolve_correlation_group("AAPL"), "TECH")
        self.assertEqual(resolve_correlation_group("BTCUSDT"), "CRYPTO_MAJOR")


if __name__ == "__main__":
    unittest.main()
