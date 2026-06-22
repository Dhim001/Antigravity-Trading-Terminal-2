"""Simulated feed lazy startup — no SBBS/yfinance in __init__."""

import time
import unittest
from unittest.mock import patch

from app.services.sim_feed import SimulatedFeedService


class TestSimFeedLazyInit(unittest.TestCase):
    @patch("app.services.sim_feed.SBBSGenerator")
    def test_init_does_not_load_sbbs(self, mock_sbbs):
        t0 = time.perf_counter()
        feed = SimulatedFeedService(tick_interval=0.01)
        elapsed = time.perf_counter() - t0
        mock_sbbs.assert_not_called()
        self.assertGreater(len(feed.symbols), 0)
        for sym in feed.symbols:
            self.assertGreater(len(feed.candles[sym]), 0)
        self.assertLess(elapsed, 2.0, "Sim feed init should be fast without SBBS")

    @patch("app.services.sim_feed.SBBSGenerator")
    def test_generators_empty_until_warm(self, mock_sbbs):
        feed = SimulatedFeedService(tick_interval=0.01)
        self.assertEqual(feed._generators, {})
        self.assertFalse(feed._sbbs_warmed)

    @patch("app.services.sim_feed.SBBSGenerator")
    def test_get_market_data_without_sbbs_does_not_crash(self, mock_sbbs):
        feed = SimulatedFeedService(tick_interval=0.01)
        sym = feed.symbols[0]
        md1 = feed.get_market_data(sym)
        md2 = feed.get_market_data(sym)
        self.assertIn("price", md1)
        self.assertIn("price", md2)
        self.assertIsInstance(md1["price"], (int, float))


if __name__ == "__main__":
    unittest.main()
