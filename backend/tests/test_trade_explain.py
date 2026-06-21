"""Tests for trade explain retrieval."""

from __future__ import annotations

import json
import unittest

from app.database import init_db
from app.db.connection import get_connection
from app.services.agent.trade_explain import explain_trade, _find_insight


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
            ("BTCUSDT:5m:1700", "BTCUSDT", 1700, payload),
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


if __name__ == "__main__":
    unittest.main()
