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
    async def test_recent_failures_reduce_by_default(self, mock_gates, mock_db):
        """Default streak mode reduces size (not hard VETO) after 3 losses."""
        mock_gates.return_value = (True, None, None)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # First fetchall: streak rows; second: win-rate scan (empty ok).
        mock_cursor.fetchall.side_effect = [
            [(-100.0,), (-50.0,), (-250.0,)],
            [(-100.0,), (-50.0,), (-250.0,)],
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        verdict = await self.intel.evaluate(self.bot, "BUY", 100.0, {}, 1783836763)

        self.assertEqual(verdict["verdict"], "REDUCE_SIZE")
        self.assertTrue(any("failures_streak" in v for v in verdict["vetoes"]))
        self.assertAlmostEqual(verdict["size_multiplier"], 0.5)
        self.assertIsNotNone(verdict.get("trade_state"))

    @patch("app.services.bots.pretrade_intel.get_connection")
    @patch("app.services.bots.pretrade_intel.check_entry_gates")
    async def test_streak_query_uses_bot_id_not_strategy(self, mock_gates, mock_db):
        """Loss streak must be scoped to this bot — not all bots with same strategy."""
        mock_gates.return_value = (True, None, None)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # Only one loss for this bot — must NOT REDUCE/VETO (fail_limit default 3).
        mock_cursor.fetchall.side_effect = [
            [(-5.0,)],
            [(-5.0,)],
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        verdict = await self.intel.evaluate(self.bot, "BUY", 100.0, {}, 1783836763)

        self.assertNotEqual(verdict["verdict"], "VETO")
        self.assertFalse(any("failures_streak" in v for v in verdict["vetoes"]))
        # SQL should bind this bot's id, not symbol+strategy fleet aggregate.
        streak_sql = mock_cursor.execute.call_args_list[0][0][0]
        streak_params = mock_cursor.execute.call_args_list[0][0][1]
        self.assertIn("bot_id = ?", streak_sql)
        self.assertEqual(streak_params[0], "bot-1")
        self.assertNotIn("b.strategy", streak_sql)

    @patch("app.services.bots.pretrade_intel.get_connection")
    @patch("app.services.bots.pretrade_intel.check_entry_gates")
    async def test_recent_failures_veto_mode(self, mock_gates, mock_db):
        """Explicit pretrade_streak_mode=veto keeps hard block."""
        mock_gates.return_value = (True, None, None)
        self.bot["config"] = {"pretrade_streak_mode": "veto"}

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [
            [(-100.0,), (-50.0,), (-250.0,)],
            [(-100.0,), (-50.0,), (-250.0,)],
        ]
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

    @patch("app.services.bots.pretrade_intel.get_aggregate_sentiment")
    @patch("app.services.bots.pretrade_intel.check_entry_gates")
    async def test_sentiment_divergence_store_keys(self, mock_gates, mock_sentiment):
        """Real store shape uses aggregate_score/mention_count."""
        mock_gates.return_value = (True, None, None)
        mock_sentiment.return_value = {"aggregate_score": -0.6, "mention_count": 5}

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
