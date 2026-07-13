"""Unit tests for the Pre-Trade Intelligence Agent."""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from app.services.bots.pretrade_intel import PreTradeIntel


class PreTradeIntelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Mock BotManager
        self.bot_manager = MagicMock()
        self.bot_manager.oms = MagicMock()
        self.bot_manager.oms.feed = MagicMock()
        self.bot_manager.screener = MagicMock()
        self.intel = PreTradeIntel(self.bot_manager)

        self.bot = {
            "id": "bot-1",
            "symbol": "AAPL",
            "strategy": "SUPERTREND_ADX",
            "timeframe": "1m",
            "config": {},
        }

    @patch("app.services.bots.pretrade_intel.check_entry_gates")
    async def test_macro_proximity_veto(self, mock_gates):
        """Pre-trade check vetoes entries near macro event blackout window."""
        mock_gates.return_value = (False, "Macro blackout FOMC", "macro")

        verdict = await self.intel.evaluate(self.bot, "BUY", 100.0, {}, 1783836763)

        self.assertEqual(verdict["verdict"], "VETO")
        self.assertTrue(any("event_policy_macro" in v for v in verdict["vetoes"]))
        self.assertEqual(verdict["size_multiplier"], 0.0)

    @patch("app.services.bots.pretrade_intel.list_bot_exposures")
    @patch("app.services.bots.pretrade_intel.summarize_basket_correlation")
    @patch("app.services.bots.pretrade_intel.check_entry_gates")
    async def test_correlation_size_reduction(self, mock_gates, mock_corr, mock_exposures):
        """Matched direction highly correlated positions trigger size reduction."""
        mock_gates.return_value = (True, None, None)
        
        # Mock active positions (MSFT is LONG with size = 100)
        mock_exposures.return_value = [
            {"bot_id": "bot-2", "symbol": "MSFT", "size": 100.0, "avg_price": 400.0}
        ]
        # Mock correlation of 0.85 between AAPL and MSFT
        mock_corr.return_value = {
            "high_pairs": [{"a": "AAPL", "b": "MSFT", "correlation": 0.85}]
        }

        verdict = await self.intel.evaluate(self.bot, "BUY", 100.0, {}, 1783836763)

        self.assertEqual(verdict["verdict"], "REDUCE_SIZE")
        self.assertTrue(any("correlation_exposure" in v for v in verdict["vetoes"]))
        self.assertEqual(verdict["size_multiplier"], 0.5)

    @patch("app.services.bots.pretrade_intel.get_connection")
    @patch("app.services.bots.pretrade_intel.check_entry_gates")
    async def test_recent_failures_veto(self, mock_gates, mock_db):
        """Veto entry if the strategy has 3 consecutive losses on the symbol in 24 hours."""
        mock_gates.return_value = (True, None, None)

        # Mock database cursor to return 3 losses (PnL < 0)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(-100.0,), (-50.0,), (-250.0,)]
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        verdict = await self.intel.evaluate(self.bot, "BUY", 100.0, {}, 1783836763)

        self.assertEqual(verdict["verdict"], "VETO")
        self.assertTrue(any("failures_streak" in v for v in verdict["vetoes"]))
        self.assertEqual(verdict["size_multiplier"], 0.0)

    @patch("app.services.bots.pretrade_intel.get_aggregate_sentiment")
    @patch("app.services.bots.pretrade_intel.check_entry_gates")
    async def test_sentiment_divergence_reduction(self, mock_gates, mock_sentiment):
        """Adverse news sentiment score reduces sizing."""
        mock_gates.return_value = (True, None, None)
        
        # Negative sentiment score of -0.6 with 5 mentions
        mock_sentiment.return_value = {"score": -0.6, "mentions": 5}

        verdict = await self.intel.evaluate(self.bot, "BUY", 100.0, {}, 1783836763)

        self.assertEqual(verdict["verdict"], "REDUCE_SIZE")
        self.assertTrue(any("sentiment_divergence" in v for v in verdict["vetoes"]))
        self.assertEqual(verdict["size_multiplier"], 0.5)

    @patch("app.services.bots.pretrade_intel.get_bot_candles")
    @patch("app.services.bots.pretrade_intel.detect_bar_anomaly")
    @patch("app.services.bots.pretrade_intel.check_entry_gates")
    async def test_anomaly_veto(self, mock_gates, mock_anomaly, mock_candles):
        """Volatility return spike anomaly or price gap vetoes entry."""
        mock_gates.return_value = (True, None, None)
        mock_candles.return_value = [{"time": i, "close": 100.0} for i in range(50)]
        self.bot_manager.screener.process_candles.return_value = pd.DataFrame([{"close": 100.0} for _ in range(50)])
        
        # Return anomaly price gap veto
        mock_anomaly.return_value = {
            "is_anomaly": True,
            "kinds": ["price_gap"],
            "gap_pct": 4.0,
        }

        verdict = await self.intel.evaluate(self.bot, "BUY", 100.0, {}, 1783836763)

        self.assertEqual(verdict["verdict"], "VETO")
        self.assertTrue(any("price_gap_anomaly" in v for v in verdict["vetoes"]))
        self.assertEqual(verdict["size_multiplier"], 0.0)
