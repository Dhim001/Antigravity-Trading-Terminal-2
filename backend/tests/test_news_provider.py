"""News feed provider tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""

_TEST_DIR = tempfile.mkdtemp()
import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "news_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH

from app.database import init_db  # noqa: E402
from app.services.altdata.news_provider import (  # noqa: E402
    SOURCE_ALPACA,
    SOURCE_FINNHUB,
    SOURCE_GNEWS,
    SOURCE_YFINANCE,
    available_news_sources,
    fetch_symbol_news,
    get_symbol_news_feed,
    normalize_news_row,
    terminal_to_alpaca_news_symbol,
)
from app.services.altdata.gnews_provider import (  # noqa: E402
    SOURCE_GNEWS as GNEWS_SOURCE,
    gnews_search_query,
)


class NewsProviderTests(unittest.TestCase):
    def setUp(self):
        db_conn._pool = None
        init_db()

    @patch("app.services.altdata.news_provider.FINNHUB_API_KEY", "key")
    def test_available_sources_includes_finnhub(self):
        sources = available_news_sources()
        self.assertIn(SOURCE_FINNHUB, sources)
        self.assertIn(SOURCE_YFINANCE, sources)

    @patch("app.services.altdata.news_provider.GNEWS_ENABLED", True)
    def test_available_sources_includes_gnews(self):
        with patch("app.config.TERMINAL_MODE", "SIMULATED"):
            sources = available_news_sources()
        self.assertIn(SOURCE_GNEWS, sources)

    @patch("app.services.altdata.news_provider.ALPACA_API_KEY", "key")
    @patch("app.services.altdata.news_provider.ALPACA_SECRET_KEY", "secret")
    @patch("app.services.altdata.news_provider.GNEWS_ENABLED", True)
    @patch("app.services.altdata.news_provider.MASSIVE_API_KEY", "poly")
    def test_live_alpaca_skips_gnews_and_polygon(self):
        with patch("app.config.TERMINAL_MODE", "LIVE_ALPACA"):
            sources = available_news_sources()
        self.assertIn(SOURCE_ALPACA, sources)
        self.assertNotIn(SOURCE_GNEWS, sources)
        self.assertNotIn("news", sources)  # Polygon SOURCE_POLYGON

    @patch("app.services.altdata.news_provider.ALPACA_API_KEY", "key")
    @patch("app.services.altdata.news_provider.ALPACA_SECRET_KEY", "secret")
    def test_available_sources_includes_alpaca(self):
        sources = available_news_sources()
        self.assertIn(SOURCE_ALPACA, sources)

    def test_terminal_to_alpaca_news_symbol(self):
        self.assertEqual(terminal_to_alpaca_news_symbol("AAPL"), "AAPL")
        self.assertEqual(terminal_to_alpaca_news_symbol("BTCUSDT"), "BTCUSD")
        self.assertEqual(terminal_to_alpaca_news_symbol("ETH/USD"), "ETHUSD")

    def test_normalize_alpaca_url_and_summary(self):
        item = normalize_news_row({
            "id": "alpaca_news:AAPL:1",
            "symbol": "AAPL",
            "source": SOURCE_ALPACA,
            "score": 0.5,
            "headline": "Apple rises",
            "published_at": "2026-07-25T12:00:00+00:00",
            "raw": {
                "url": "https://www.benzinga.com/example",
                "summary": "Shares advanced after guidance.",
                "headline": "Apple rises",
            },
        })
        self.assertEqual(item["source_label"], "Alpaca")
        self.assertEqual(item["url"], "https://www.benzinga.com/example")
        self.assertIn("guidance", item["summary"] or "")

    @patch("app.services.altdata.news_provider.fetch_polygon_news", return_value=[])
    @patch("app.services.altdata.news_provider.fetch_gnews_news", return_value=[])
    @patch("app.services.altdata.news_provider.fetch_yfinance_news", return_value=[])
    @patch("app.services.altdata.news_provider.fetch_finnhub_company_news", return_value=[])
    @patch("app.services.altdata.news_provider.fetch_alpaca_news")
    def test_fetch_includes_alpaca(self, mock_alpaca, _finn, _yf, _gnews, _poly):
        mock_alpaca.return_value = [{
            "id": "alpaca_news:TSLA:99",
            "symbol": "TSLA",
            "source": SOURCE_ALPACA,
            "score": 0.25,
            "mention_count": 1,
            "headline": "Tesla delivery beat",
            "published_at": "2026-07-25T10:00:00+00:00",
            "raw": {"url": "https://example.com/tsla", "summary": "Deliveries rose"},
        }]
        with patch("app.services.altdata.news_provider.ALPACA_API_KEY", "key"), patch(
            "app.services.altdata.news_provider.ALPACA_SECRET_KEY", "secret"
        ):
            rows = fetch_symbol_news("TSLA", sources=[SOURCE_ALPACA])
        self.assertEqual(len(rows), 1)
        item = normalize_news_row(rows[0])
        self.assertEqual(item["source_label"], "Alpaca")
        self.assertEqual(item["url"], "https://example.com/tsla")

    @patch("app.services.altdata.news_provider.fetch_polygon_news", return_value=[])
    @patch("app.services.altdata.news_provider.fetch_gnews_news", return_value=[])
    @patch("app.services.altdata.news_provider.fetch_yfinance_news")
    @patch("app.services.altdata.news_provider.fetch_finnhub_company_news")
    def test_fetch_dedupes_headlines(self, mock_finn, mock_yf, _gnews, _poly):
        mock_finn.return_value = [{
            "id": "finnhub_news:AAPL:1",
            "symbol": "AAPL",
            "source": SOURCE_FINNHUB,
            "score": 0.4,
            "mention_count": 1,
            "headline": "Apple beats estimates",
            "published_at": "2026-06-02T12:00:00+00:00",
            "raw": {"url": "https://example.com/aapl", "summary": "Strong quarter"},
        }]
        mock_yf.return_value = [{
            "id": "yfinance_news:AAPL:2",
            "symbol": "AAPL",
            "source": SOURCE_YFINANCE,
            "score": 0.3,
            "mention_count": 1,
            "headline": "Apple beats estimates",
            "published_at": "2026-06-01T12:00:00+00:00",
            "raw": {"link": "https://example.com/dup"},
        }]
        with patch("app.services.altdata.news_provider.FINNHUB_API_KEY", "key"):
            rows = fetch_symbol_news("AAPL", sources=[SOURCE_FINNHUB, SOURCE_YFINANCE])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], SOURCE_FINNHUB)

    def test_normalize_extracts_url_and_summary(self):
        item = normalize_news_row({
            "id": "finnhub_news:AAPL:1",
            "symbol": "AAPL",
            "source": SOURCE_FINNHUB,
            "score": 0.2,
            "headline": "Headline",
            "published_at": "2026-06-01T12:00:00+00:00",
            "raw": {"url": "https://finnhub.io/x", "summary": "Details here"},
        })
        self.assertEqual(item["url"], "https://finnhub.io/x")
        self.assertEqual(item["summary"], "Details here")
        self.assertEqual(item["source_label"], "Finnhub")

    def test_yfinance_nested_content_parsed(self):
        from app.services.altdata.news_provider import _flatten_yfinance_item

        nested = {
            "id": "abc",
            "content": {
                "title": "Bitcoin slides on strategy pivot",
                "summary": "Crypto markets weakened today.",
                "pubDate": "2026-06-30T22:19:19Z",
                "clickThroughUrl": {"url": "https://finance.yahoo.com/example"},
            },
        }
        flat = _flatten_yfinance_item(nested)
        self.assertEqual(flat["title"], "Bitcoin slides on strategy pivot")
        self.assertEqual(flat["link"], "https://finance.yahoo.com/example")
        item = normalize_news_row({
            "id": "yfinance_news:SOLUSDT:1",
            "symbol": "SOLUSDT",
            "source": SOURCE_YFINANCE,
            "score": 0.1,
            "headline": flat["title"],
            "published_at": "2026-06-30T22:19:19+00:00",
            "raw": flat,
        })
        self.assertEqual(item["url"], "https://finance.yahoo.com/example")
        self.assertIn("Crypto markets", item["summary"] or "")

    @patch("app.services.altdata.news_provider.fetch_polygon_news", return_value=[])
    @patch("app.services.altdata.news_provider.fetch_yfinance_news", return_value=[])
    @patch("app.services.altdata.news_provider.fetch_finnhub_company_news", return_value=[])
    @patch("app.services.altdata.news_provider.fetch_gnews_news")
    def test_fetch_includes_gnews(self, mock_gnews, _finn, _yf, _poly):
        mock_gnews.return_value = [{
            "id": "gnews:ADAUSDT:1",
            "symbol": "ADAUSDT",
            "source": SOURCE_GNEWS,
            "score": -0.2,
            "mention_count": 1,
            "headline": "Cardano network upgrade",
            "published_at": "2026-07-06T12:00:00+00:00",
            "raw": {
                "url": "https://example.com/ada",
                "description": "Upgrade details",
                "title": "Cardano network upgrade",
            },
        }]
        with patch("app.services.altdata.news_provider.GNEWS_ENABLED", True):
            rows = fetch_symbol_news("ADAUSDT", sources=[SOURCE_GNEWS])
        self.assertEqual(len(rows), 1)
        item = normalize_news_row(rows[0])
        self.assertEqual(item["source_label"], "Google News")
        self.assertEqual(item["url"], "https://example.com/ada")
        self.assertEqual(item["summary"], "Upgrade details")

    def test_gnews_search_query_crypto_and_equity(self):
        self.assertEqual(gnews_search_query("ADAUSDT"), "Cardano cryptocurrency")
        self.assertEqual(gnews_search_query("AAPL"), "AAPL stock")
        self.assertEqual(GNEWS_SOURCE, SOURCE_GNEWS)

    @patch("app.services.altdata.news_provider.fetch_symbol_news")
    def test_get_feed_refresh_persists(self, mock_fetch):
        mock_fetch.return_value = [{
            "id": "yfinance_news:MSFT:1",
            "symbol": "MSFT",
            "source": SOURCE_YFINANCE,
            "score": 0.1,
            "mention_count": 1,
            "headline": "Microsoft cloud growth",
            "published_at": "2026-06-01T12:00:00+00:00",
            "raw": {"link": "https://example.com/msft"},
        }]
        with patch("app.services.altdata.news_provider.SENTIMENT_ENABLED", True):
            feed = get_symbol_news_feed("MSFT", refresh=True, limit=10)
        self.assertEqual(feed["symbol"], "MSFT")
        self.assertEqual(len(feed["items"]), 1)
        self.assertEqual(feed["items"][0]["headline"], "Microsoft cloud growth")

    @patch("app.services.altdata.news_provider.fetch_alpaca_top_headlines")
    @patch("app.services.altdata.news_provider.fetch_alpaca_news_for_tickers")
    @patch("app.services.altdata.alpaca_screener.fetch_alpaca_market_snapshot")
    def test_market_news_feed_combines_movers_and_headlines(
        self, mock_snap, mock_mover_news, mock_top
    ):
        from app.services.altdata.news_provider import get_market_news_feed

        mock_snap.return_value = {
            "stocks": {
                "gainers": [{
                    "symbol": "NVDA",
                    "terminal_symbol": "NVDA",
                    "news_symbol": "NVDA",
                    "side": "gainer",
                    "market_type": "stocks",
                    "percent_change": 5.0,
                }],
                "losers": [],
            },
            "crypto": {"gainers": [], "losers": []},
            "most_actives": [],
        }
        mock_mover_news.return_value = [{
            "id": "alpaca_news:NVDA:1",
            "symbol": "NVDA",
            "source": SOURCE_ALPACA,
            "score": 0.4,
            "mention_count": 1,
            "headline": "Nvidia AI demand surge",
            "published_at": "2026-07-25T10:00:00+00:00",
            "raw": {"url": "https://example.com/nvda", "symbols": ["NVDA"]},
            "related_symbols": ["NVDA"],
        }]
        mock_top.return_value = [{
            "id": "alpaca_news:MARKET:2",
            "symbol": "MARKET",
            "source": SOURCE_ALPACA,
            "score": 0.0,
            "mention_count": 1,
            "headline": "Markets open mixed",
            "published_at": "2026-07-25T09:00:00+00:00",
            "raw": {"url": "https://example.com/mkt", "symbols": []},
            "related_symbols": [],
        }]
        with patch("app.services.altdata.news_provider.SENTIMENT_ENABLED", True):
            feed = get_market_news_feed(top=5, limit=20, persist=False)
        self.assertEqual(feed["scope"], "market")
        self.assertEqual(len(feed["items"]), 2)
        self.assertEqual(feed["items"][0]["headline"], "Nvidia AI demand surge")
        self.assertIn("NVDA", feed["mover_news_symbols"])
        self.assertEqual(feed["movers"]["stocks"]["gainers"][0]["symbol"], "NVDA")

    def test_screener_symbol_mapping(self):
        from app.services.altdata.alpaca_screener import (
            screener_symbol_to_news_ticker,
            screener_symbol_to_terminal,
        )
        self.assertEqual(screener_symbol_to_news_ticker("WIF/USD"), "WIFUSD")
        self.assertEqual(screener_symbol_to_terminal("BTC/USD"), "BTCUSDT")
        self.assertEqual(screener_symbol_to_news_ticker("AAPL"), "AAPL")


if __name__ == "__main__":
    unittest.main()
