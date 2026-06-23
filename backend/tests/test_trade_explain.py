"""Tests for trade explain retrieval."""

from __future__ import annotations

import json
import unittest

from app.database import init_db
from app.db.connection import get_connection
from app.services.agent.trade_explain import explain_trade, _find_insight, _fetch_trade_relevant_logs


class TestTradeExplain(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bot_trades")
        cursor.execute("DELETE FROM agent_insights")
        cursor.execute("DELETE FROM bots WHERE id = 'bot-explain-1'")
        conn.commit()
        conn.close()

    async def test_find_insight_matches_timeframe(self):
        conn = get_connection()
        cursor = conn.cursor()
        payload = json.dumps({
            "symbol": "BTCUSDT",
            "bar_time": 1700,
            "timeframe": "5m",
            "signal": "BUY",
            "confidence": 0.7,
            "reasons": ["MACD bullish"],
            "insight_id": "BTCUSDT:5m:1700",
        })
        cursor.execute(
            """
            INSERT INTO agent_insights (insight_id, symbol, bar_time, payload, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            ("BTCUSDT:5m:1700-nearest", "BTCUSDT", 1700, payload),
        )
        conn.commit()
        conn.close()

        hit = _find_insight("BTCUSDT", 1700, "5m")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["timeframe"], "5m")

        miss = _find_insight("BTCUSDT", 1700, "1m")
        self.assertIsNone(miss)

    async def test_explain_trade_uses_stored_snapshot(self):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config, execution_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("bot-explain-1", "CHART_AGENT", "BTCUSDT", "1m", "STOPPED", 1000, "{}", "BAR_CLOSE"),
        )
        snapshot = {
            "signal": "BUY",
            "confidence": 0.72,
            "score": 2,
            "reasons": ["Stored reason"],
            "sub_reports": {"trend": {"score": 1}},
        }
        cursor.execute(
            """
            INSERT INTO bot_trades
            (bot_id, order_id, symbol, side, quantity, price, pnl, signal_id, signal_bar_time, is_exit, insight_snapshot)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bot-explain-1", "o1", "BTCUSDT", "BUY", 0.1, 100.0, None,
                "bot-explain-1:1700:BUY", 1700, 0, json.dumps(snapshot),
            ),
        )
        conn.commit()
        cursor.execute("SELECT id FROM bot_trades WHERE bot_id = 'bot-explain-1'")
        trade_id = str(cursor.fetchone()[0])
        conn.close()

        result = await explain_trade("bot-explain-1", trade_id)
        self.assertEqual(result["insight"]["signal"], "BUY")
        self.assertEqual(result["insight"]["reasons"][0], "Stored reason")

    async def test_trade_relevant_logs_prioritize_signal_context(self):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config, execution_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("bot-log-1", "CHART_AGENT", "BTCUSDT", "1m", "STOPPED", 1000, "{}", "BAR_CLOSE"),
        )
        for msg in ("Heartbeat ok", "BTCUSDT signal BUY at bar 1700", "Unrelated maintenance"):
            cursor.execute(
                "INSERT INTO bot_logs (bot_id, level, message) VALUES (?, ?, ?)",
                ("bot-log-1", "INFO", msg),
            )
        conn.commit()
        conn.close()
        ranked = _fetch_trade_relevant_logs(
            {"symbol": "BTCUSDT", "side": "BUY", "signal_bar_time": 1700},
            "bot-log-1",
            limit=2,
        )
        self.assertTrue(ranked)
        self.assertIn("signal", ranked[0].lower())

    async def test_find_insight_nearest_bar_within_period(self):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM agent_insights WHERE insight_id = 'BTCUSDT:5m:1700-nearest'")
        payload = json.dumps({
            "symbol": "BTCUSDT",
            "bar_time": 1700,
            "timeframe": "5m",
            "signal": "BUY",
            "confidence": 0.7,
        })
        cursor.execute(
            """
            INSERT INTO agent_insights (insight_id, symbol, bar_time, payload, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            ("BTCUSDT:5m:1700-nearest", "BTCUSDT", 1700, payload),
        )
        conn.commit()
        conn.close()

        hit = _find_insight("BTCUSDT", 1704, "5m")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["bar_time"], 1700)

    async def test_explain_exit_links_entry_insight(self):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bot_trades WHERE bot_id = 'bot-exit-1'")
        cursor.execute("DELETE FROM bots WHERE id = 'bot-exit-1'")
        cursor.execute(
            """
            INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config, execution_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("bot-exit-1", "CHART_AGENT", "BTCUSDT", "5m", "STOPPED", 1000, "{}", "BAR_CLOSE"),
        )
        entry_snapshot = {
            "signal": "BUY",
            "confidence": 0.8,
            "reasons": ["Entry reason"],
            "timeframe": "5m",
        }
        cursor.execute(
            """
            INSERT INTO bot_trades
            (bot_id, order_id, symbol, side, quantity, price, pnl, signal_id, signal_bar_time, is_exit, insight_snapshot, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bot-exit-1", "o-entry", "BTCUSDT", "BUY", 0.1, 100.0, None,
                "bot-exit-1:1700:BUY", 1700, 0, json.dumps(entry_snapshot), "2026-01-01T10:00:00Z",
            ),
        )
        cursor.execute(
            """
            INSERT INTO bot_trades
            (bot_id, order_id, symbol, side, quantity, price, pnl, signal_id, signal_bar_time, is_exit, insight_snapshot, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bot-exit-1", "o-exit", "BTCUSDT", "SELL", 0.1, 101.0, 0.1,
                "bot-exit-1:1800:SELL", 1800, 1, None, "2026-01-01T11:00:00Z",
            ),
        )
        conn.commit()
        cursor.execute("SELECT id FROM bot_trades WHERE order_id = 'o-exit'")
        trade_id = str(cursor.fetchone()[0])
        conn.close()

        result = await explain_trade("bot-exit-1", trade_id)
        self.assertTrue(result["trade"]["is_exit"])
        self.assertEqual(result["insight"]["signal"], "BUY")
        self.assertIn("Entry reason", result["summary"])


if __name__ == "__main__":
    unittest.main()
