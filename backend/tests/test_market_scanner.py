"""Market scanner batch service tests."""

import unittest

from app.services.scanner.market_scanner import MarketScannerService


class MarketScannerFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_signal_filter_buy_matches_uppercase_insights(self):
        scanner = MarketScannerService()
        baseline = await scanner.scan(["BTCUSDT", "ETHUSDT", "MSFT", "TSLA"], signal_filter="any")
        self.assertGreater(baseline["count"], 0)

        buy_rows = [r for r in baseline["rows"] if r["signal"] == "BUY"]
        if not buy_rows:
            self.skipTest("No BUY signals in current fixture data")

        filtered = await scanner.scan(
            [r["symbol"] for r in buy_rows],
            signal_filter="buy",
        )
        self.assertEqual(filtered["count"], len(buy_rows))
        self.assertTrue(all(r["signal"] == "BUY" for r in filtered["rows"]))

    async def test_signal_filter_none_excludes_actionable(self):
        scanner = MarketScannerService()
        result = await scanner.scan(["BTCUSDT", "ETHUSDT"], signal_filter="NONE")
        self.assertTrue(all(r["signal"] == "NONE" for r in result["rows"]))


if __name__ == "__main__":
    unittest.main()
