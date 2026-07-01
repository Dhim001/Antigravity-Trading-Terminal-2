"""Sentiment feed — lexicon, store, rule engine, and bot filters."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""
os.environ["SENTIMENT_ENABLED"] = "true"

_TEST_DIR = tempfile.mkdtemp()
import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "sentiment_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH

from app.database import init_db  # noqa: E402
from app.services.altdata.sentiment_lexicon import score_text_sentiment  # noqa: E402
from app.services.altdata.store import (  # noqa: E402
    get_aggregate_sentiment,
    upsert_sentiment_events,
)
from app.services.bots.strategies_chart_agent import check_entry_filters  # noqa: E402


class SentimentLexiconTests(unittest.TestCase):
    def test_bullish_headline(self):
        self.assertGreater(score_text_sentiment("Stock surges on strong earnings beat"), 0.0)

    def test_bearish_headline(self):
        self.assertLess(score_text_sentiment("Shares plunge after profit warning and downgrade"), 0.0)

    def test_neutral_headline(self):
        self.assertEqual(score_text_sentiment("Company schedules annual meeting"), 0.0)


class SentimentStoreTests(unittest.TestCase):
    def setUp(self):
        db_conn._pool = None
        init_db()

    def test_aggregate_sentiment(self):
        upsert_sentiment_events([
            {
                "id": "news:AAPL:1",
                "symbol": "AAPL",
                "source": "news",
                "score": 0.8,
                "mention_count": 1,
                "headline": "Apple stock surges on beat",
                "published_at": "2026-06-01T12:00:00+00:00",
            },
            {
                "id": "news:AAPL:2",
                "symbol": "AAPL",
                "source": "news",
                "score": 0.4,
                "mention_count": 1,
                "headline": "Analyst upgrade lifts Apple",
                "published_at": "2026-06-01T13:00:00+00:00",
            },
        ])
        agg = get_aggregate_sentiment("AAPL", lookback_hours=48)
        self.assertEqual(agg["mention_count"], 2)
        self.assertGreater(agg["aggregate_score"], 0.5)


class SentimentFilterTests(unittest.TestCase):
    def test_blocks_buy_on_bearish_sentiment(self):
        insight = {
            "score": 3,
            "confidence": 0.8,
            "sub_reports": {
                "sentiment": {"aggregate_score": -0.5, "score": -1},
            },
        }
        reason = check_entry_filters(
            insight,
            {"sentiment_filter_enabled": True, "min_sentiment_score": 0.1},
            "BUY",
        )
        self.assertIsNotNone(reason)
        self.assertIn("sentiment", reason.lower())

    def test_allows_buy_when_filter_disabled(self):
        insight = {
            "score": 3,
            "sub_reports": {"sentiment": {"aggregate_score": -0.5}},
        }
        reason = check_entry_filters(
            insight,
            {"sentiment_filter_enabled": False},
            "BUY",
        )
        self.assertIsNone(reason)


class SentimentRuleEngineTests(unittest.TestCase):
    def setUp(self):
        db_conn._pool = None
        init_db()
        upsert_sentiment_events([
            {
                "id": "news:MSFT:1",
                "symbol": "MSFT",
                "source": "news",
                "score": 1.0,
                "mention_count": 1,
                "headline": "Microsoft stock surges on strong cloud growth beat",
                "published_at": "2026-06-01T12:00:00+00:00",
            },
        ])

    def test_sub_report_includes_sentiment(self):
        import pandas as pd
        from app.services.agent.rule_engine import score_dataframe

        n = 35
        df = pd.DataFrame({
            "time": list(range(n)),
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.0 + i * 0.01 for i in range(n)],
            "volume": [1000.0] * n,
        })
        insight = score_dataframe(df, "MSFT")
        self.assertIsNotNone(insight)
        sent = (insight.sub_reports or {}).get("sentiment")
        self.assertIsNotNone(sent)
        self.assertGreater(sent.get("aggregate_score", 0), 0)


class SentimentProviderMergeTests(unittest.TestCase):
    @patch("app.services.altdata.sentiment_provider.fetch_finnhub_sentiment")
    @patch("app.services.altdata.sentiment_provider.fetch_polygon_news", return_value=[])
    @patch("app.services.altdata.sentiment_provider.fetch_yfinance_news", return_value=[])
    def test_finnhub_merged_when_configured(self, _yf, _massive, mock_finn):
        mock_finn.return_value = [{
            "id": "finnhub_news:AAPL:1",
            "symbol": "AAPL",
            "source": "finnhub_news",
            "score": 0.5,
            "mention_count": 1,
            "headline": "Beat",
            "published_at": "2026-06-01T12:00:00+00:00",
            "raw": {},
        }]
        with patch("app.services.altdata.sentiment_provider.FINNHUB_API_KEY", "key"):
            from app.services.altdata.sentiment_provider import fetch_symbol_sentiment

            rows = fetch_symbol_sentiment("AAPL")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "finnhub_news")


if __name__ == "__main__":
    unittest.main()
