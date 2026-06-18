"""ChartAnalystService — idempotency, cache, persistence, audit."""

from __future__ import annotations

import asyncio
import logging
import unittest
from unittest.mock import AsyncMock, patch

from app.db.connection import get_connection
from app.database import init_db
from app.services.agent.chart_analyst import ChartAnalystService, init_chart_analyst
from app.services.agent.feature_builder import FeatureBuilder
from app.services.bots.screener import MarketScreenerService
from tests.test_chart_agent_rules import make_trending_candles


class TestChartAnalyst(unittest.TestCase):
    def setUp(self):
        init_db()
        self.candles = make_trending_candles(220)
        self.analyst = ChartAnalystService(MarketScreenerService())

    def test_insight_idempotent_same_bar(self):
        df = FeatureBuilder(self.analyst.feature_builder.screener).build("BTCUSDT", self.candles)
        from app.services.agent.rule_engine import score_dataframe

        first = score_dataframe(df, "BTCUSDT")
        second = score_dataframe(df, "BTCUSDT")
        self.assertEqual(first.insight_id, second.insight_id)
        self.assertEqual(first.score, second.score)
        self.assertEqual(first.signal, second.signal)

    def test_persist_and_list_roundtrip(self):
        symbol = "TESTAGENT_PERSIST"
        df = FeatureBuilder(self.analyst.feature_builder.screener).build(symbol, self.candles)
        from app.services.agent.rule_engine import score_dataframe

        insight = score_dataframe(df, symbol)
        self.assertIsNotNone(insight)
        self.analyst.persist(insight)
        rows = self.analyst.list_insights(symbol, limit=5)
        self.assertTrue(rows)
        self.assertIn(insight.insight_id, [r["insight_id"] for r in rows])

    def test_cache_hit_after_analyze(self):
        async def _run():
            with patch.object(ChartAnalystService, "persist", lambda self, insight: None):
                with patch.object(
                    ChartAnalystService,
                    "_audit",
                    lambda *a, **k: None,
                ):
                    insight = await self.analyst.analyze(
                        "BTCUSDT",
                        candles=self.candles,
                        broadcast=False,
                    )
            cached = self.analyst.get_cached("BTCUSDT")
            self.assertIsNotNone(cached)
            self.assertEqual(cached["insight_id"], insight.insight_id)
            return insight

        insight = asyncio.run(_run())
        self.assertIsNotNone(insight)

    def test_init_singleton(self):
        svc = init_chart_analyst(screener=MarketScreenerService(), feed=None, broadcast_fn=None)
        from app.services.agent.chart_analyst import get_chart_analyst

        self.assertIs(get_chart_analyst(), svc)

    def test_audit_log_emits_required_fields(self):
        async def _run():
            with self.assertLogs("app.services.agent.chart_analyst", level=logging.INFO) as logs:
                with patch.object(ChartAnalystService, "persist", lambda self, insight: None):
                    insight = await self.analyst.analyze(
                        "BTCUSDT",
                        candles=self.candles,
                        broadcast=False,
                    )
            return insight, logs

        insight, logs = asyncio.run(_run())
        self.assertIsNotNone(insight)
        audit_lines = [r.message for r in logs.records if "agent_audit" in r.message]
        self.assertTrue(audit_lines)
        line = audit_lines[0]
        self.assertIn(f"insight_id={insight.insight_id}", line)
        self.assertIn("signal=", line)
        self.assertIn("confidence=", line)
        self.assertIn("llm_called=", line)
        self.assertIn("latency_ms=", line)


if __name__ == "__main__":
    unittest.main()
