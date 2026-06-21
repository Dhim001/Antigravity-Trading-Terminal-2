"""HTTP/WS round-trip for chart_analyze action and insight history."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient

from app.api.http.app import create_http_app
from app.api.http.dispatch import invoke_action
from app.api.protocol import Action, MessageType
from app.api.router import ensure_routes_loaded
from app.api.state import AppState
from app.services.agent.models import ChartAgentInsight
from tests.test_chart_agent_rules import make_trending_candles


def _insight_payload() -> dict:
    insight = ChartAgentInsight(
        symbol="BTCUSDT",
        bar_time=1_700_000_000,
        signal="BUY",
        confidence=0.75,
        score=3,
        reasons=["MACD above signal"],
        levels={"stop_loss_distance": 1.5},
    )
    return insight.to_dict()


class TestChartAnalyzeApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_routes_loaded()
        chart_analyst = MagicMock()
        insight = ChartAgentInsight(**{**_insight_payload(), "narrative": None, "model": None})
        chart_analyst.analyze = AsyncMock(return_value=insight)

        cls.state = AppState(
            oms=MagicMock(),
            manager=MagicMock(),
            bot_manager=MagicMock(),
            chart_analyst=chart_analyst,
        )
        cls.client = TestClient(create_http_app(cls.state))

    def test_chart_analyze_binding_registered(self):
        from app.api.router import ROUTES
        self.assertIn(Action.CHART_ANALYZE, ROUTES)

    def test_post_agent_analyze_returns_insight(self):
        resp = self.client.post(
            "/api/v1/agent/analyze",
            json={"symbol": "BTCUSDT"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("type"), "agent_insight")
        self.assertEqual(body["data"]["symbol"], "BTCUSDT")
        self.assertIn(body["data"]["signal"], ("BUY", "SELL", "NONE"))

    def test_get_agent_insights_list(self):
        insights = [_insight_payload(), {**_insight_payload(), "insight_id": "BTCUSDT:1700000060"}]
        self.state.chart_analyst.list_insights = MagicMock(return_value=insights)

        resp = self.client.get("/api/v1/agent/insights/BTCUSDT?limit=10")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("symbol"), "BTCUSDT")
        self.assertEqual(body.get("count"), 2)
        self.assertEqual(len(body.get("insights", [])), 2)
        self.state.chart_analyst.list_insights.assert_called_once_with(
            "BTCUSDT", limit=10, timeframe=None,
        )

    def test_get_agent_insights_list_with_timeframe(self):
        self.state.chart_analyst.list_insights = MagicMock(return_value=[])

        resp = self.client.get("/api/v1/agent/insights/BTCUSDT?limit=5&timeframe=5m")
        self.assertEqual(resp.status_code, 200)
        self.state.chart_analyst.list_insights.assert_called_once_with(
            "BTCUSDT", limit=5, timeframe="5m",
        )

    def test_get_agent_insights_unavailable(self):
        state = AppState(
            oms=MagicMock(),
            manager=MagicMock(),
            bot_manager=MagicMock(),
            chart_analyst=None,
        )
        client = TestClient(create_http_app(state))
        resp = client.get("/api/v1/agent/insights/BTCUSDT")
        self.assertEqual(resp.status_code, 503)


class TestChartAnalyzeDispatch(unittest.IsolatedAsyncioTestCase):
    """WS action path via invoke_action (HTTP collector simulates WS reply)."""

    async def asyncSetUp(self):
        ensure_routes_loaded()
        from app.services.agent.chart_analyst import ChartAnalystService
        from app.services.bots.screener import MarketScreenerService

        self.analyst = ChartAnalystService(MarketScreenerService())
        self.state = AppState(
            oms=MagicMock(),
            manager=MagicMock(),
            bot_manager=MagicMock(),
            chart_analyst=self.analyst,
        )
        self.candles = make_trending_candles(220)

    async def test_invoke_chart_analyze_returns_agent_insight(self):
        with patch.object(self.analyst, "persist", lambda insight: None):
            messages = await invoke_action(
                self.state,
                Action.CHART_ANALYZE.value,
                {"symbol": "BTCUSDT", "_rate_key": "test-parity"},
            )
        types = [m.get("type") for m in messages]
        self.assertIn(MessageType.AGENT_INSIGHT.value, types)
        insight_msg = next(m for m in messages if m.get("type") == MessageType.AGENT_INSIGHT.value)
        data = insight_msg["data"]
        self.assertEqual(data["symbol"], "BTCUSDT")
        self.assertIn(data["signal"], ("BUY", "SELL", "NONE"))
        self.assertIn("insight_id", data)
        self.assertIn("confidence", data)

    async def test_invoke_chart_analyze_rate_limited(self):
        key = {"symbol": "BTCUSDT", "_rate_key": "test-rate-limit"}
        with patch.object(self.analyst, "persist", lambda insight: None):
            first = await invoke_action(self.state, Action.CHART_ANALYZE.value, key)
            second = await invoke_action(self.state, Action.CHART_ANALYZE.value, key)
        self.assertTrue(any(m.get("type") == MessageType.AGENT_INSIGHT.value for m in first))
        self.assertTrue(any(m.get("type") == MessageType.ERROR.value for m in second))


if __name__ == "__main__":
    unittest.main()
