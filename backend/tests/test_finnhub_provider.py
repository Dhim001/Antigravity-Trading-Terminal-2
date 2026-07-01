"""Finnhub sentiment provider tests."""

import unittest
from unittest.mock import patch

from app.services.altdata.finnhub_provider import (
    fetch_finnhub_company_news,
    fetch_finnhub_news_sentiment,
    fetch_finnhub_sentiment,
)


class FinnhubProviderTests(unittest.TestCase):
    @patch("app.services.altdata.finnhub_provider.FINNHUB_API_KEY", "test-key")
    @patch("app.services.altdata.finnhub_provider._get_json")
    def test_company_news_parsed(self, mock_get):
        mock_get.return_value = [
            {
                "id": 1,
                "headline": "Apple stock surges on earnings beat",
                "summary": "Strong growth reported",
                "datetime": 1_700_000_000,
            },
        ]
        rows = fetch_finnhub_company_news("AAPL")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "finnhub_news")
        self.assertGreater(rows[0]["score"], 0)

    @patch("app.services.altdata.finnhub_provider.FINNHUB_API_KEY", "test-key")
    @patch("app.services.altdata.finnhub_provider._get_json")
    def test_news_sentiment_aggregate(self, mock_get):
        mock_get.return_value = {
            "sentiment": {"bullishPercent": 60, "bearishPercent": 20},
            "buzz": {"articlesInLastWeek": 5},
        }
        rows = fetch_finnhub_news_sentiment("MSFT")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "finnhub_sentiment")
        self.assertAlmostEqual(rows[0]["score"], 0.4, places=2)

    def test_crypto_skipped(self):
        self.assertEqual(fetch_finnhub_sentiment("BTCUSDT"), [])


if __name__ == "__main__":
    unittest.main()
