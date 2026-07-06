"""Unit tests for Google News provider."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""

from app.services.altdata.gnews_provider import (  # noqa: E402
    SOURCE_GNEWS,
    _parse_gnews_published,
    fetch_gnews_news,
    gnews_search_query,
)


class GNewsProviderTests(unittest.TestCase):
    def test_parse_gnews_published_rfc822(self):
        iso = _parse_gnews_published({"published date": "Mon, 06 Jul 2026 10:00:00 GMT"})
        self.assertIn("2026-07-06", iso)

    def test_parse_gnews_published_iso(self):
        iso = _parse_gnews_published({"published date": "2026-07-06T10:00:00Z"})
        self.assertIn("2026-07-06", iso)

    @patch("app.services.altdata.gnews_provider.GNEWS_ENABLED", False)
    def test_fetch_disabled_returns_empty(self):
        self.assertEqual(fetch_gnews_news("AAPL"), [])

    @patch("app.services.altdata.gnews_provider.GNEWS_ENABLED", True)
    def test_fetch_maps_articles(self):
        mock_client = MagicMock()
        mock_client.get_news.return_value = [{
            "title": "Apple unveils new chip",
            "description": "Silicon details revealed.",
            "url": "https://example.com/apple",
            "published date": "Mon, 06 Jul 2026 12:00:00 GMT",
        }]
        mock_gnews_cls = MagicMock(return_value=mock_client)
        with patch.dict("sys.modules", {"gnews": MagicMock(GNews=mock_gnews_cls)}):
            rows = fetch_gnews_news("AAPL")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], SOURCE_GNEWS)
        self.assertEqual(rows[0]["headline"], "Apple unveils new chip")
        mock_client.get_news.assert_called_once_with("AAPL stock")

    def test_gnews_search_query_spy(self):
        self.assertEqual(gnews_search_query("SPY"), "S&P 500 ETF")


if __name__ == "__main__":
    unittest.main()
