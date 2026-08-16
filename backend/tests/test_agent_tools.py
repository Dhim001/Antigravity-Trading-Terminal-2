"""Unit tests for the shared agent ToolRegistry and its HTTP surface."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Isolate SQLite before importing app modules that touch DB.
_TEST_DIR = tempfile.mkdtemp(prefix="agent_tools_test_")
_TEST_DB = os.path.join(_TEST_DIR, "agent_tools.db")
os.environ["DB_PATH"] = _TEST_DB

import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = _TEST_DB

from app.database import init_db  # noqa: E402

init_db()

from app.services.agent.copilot import confirm_action, handle_message, pop_pending  # noqa: E402
from app.services.agent.tools.catalog import build_registry, get_registry  # noqa: E402
from app.services.agent.tools.registry import AgentTool, ToolContext  # noqa: E402

EXPECTED_TOOLS = {
    "analyze_symbol",
    "meta_insight",
    "recommend_strategy",
    "scan_market",
    "get_portfolio_status",
    "list_bots",
    "get_bot_performance",
    "get_sentiment",
    "run_backtest",
    "explain_trade",
    "explain_bot_events",
    "deploy_bot",
    "pause_bot",
    "stop_bot",
    "pause_all_bots",
    "stop_all_bots",
    "update_bot_config",
    "help",
}

GATED_TOOLS = {"deploy_bot", "pause_bot", "stop_bot", "pause_all_bots", "stop_all_bots", "update_bot_config"}


def _make_state() -> MagicMock:
    state = MagicMock()
    state.oms = MagicMock()
    state.bot_manager = MagicMock()
    state.bot_manager.active_bots = {}
    state.bot_manager.list_bots_public.return_value = []
    state.bot_manager.create_bot = AsyncMock(return_value="bot-xyz")
    state.bot_manager.pause_bot = AsyncMock()
    state.bot_manager.stop_bot = AsyncMock()
    state.bot_manager.stop_all_bots = AsyncMock()
    state.chart_analyst = None
    return state


class CatalogTests(unittest.TestCase):
    def test_catalog_registers_all_copilot_tools(self):
        registry = get_registry()
        names = {t.name for t in registry.list()}
        self.assertEqual(names, EXPECTED_TOOLS)
        for tool in registry.list():
            self.assertIn(tool.side_effect, ("read", "trade", "control"))
            self.assertTrue(tool.description)
            self.assertEqual(tool.input_schema.get("type"), "object")
            self.assertEqual(tool.hitl_required, tool.name in GATED_TOOLS)
            self.assertEqual(
                tool.side_effect in ("trade", "control"), tool.name in GATED_TOOLS
            )

    def test_to_dict_shape(self):
        tool = get_registry().get("analyze_symbol")
        d = tool.to_dict()
        for key in ("name", "description", "input_schema", "side_effect", "hitl_required"):
            self.assertIn(key, d)
        self.assertIn("symbol", d["input_schema"]["properties"])

    def test_get_unknown_returns_none(self):
        self.assertIsNone(get_registry().get("not_a_tool"))

    def test_register_rejects_bad_side_effect(self):
        with self.assertRaises(ValueError):
            build_registry().register(
                AgentTool(name="x", description="d", side_effect="nuke")
            )


class RegistryExecuteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = build_registry()
        self.state = _make_state()
        self.ctx = ToolContext(state=self.state, session_id="agent-tools-test")

    async def test_unknown_tool(self):
        res = await self.registry.execute("not_a_tool", {}, context=self.ctx)
        self.assertFalse(res["ok"])
        self.assertIn("Unknown tool", res["error"])

    async def test_schema_validation_rejects_bad_type(self):
        res = await self.registry.execute("scan_market", {"limit": {"bad": 1}}, context=self.ctx)
        self.assertFalse(res["ok"])
        self.assertIn("limit", res["error"])

    async def test_schema_validation_missing_required_on_execute(self):
        res = await self.registry.execute("pause_bot", {}, confirmed=True, context=self.ctx)
        self.assertFalse(res["ok"])
        self.assertIn("bot_id", res["error"])
        self.state.bot_manager.pause_bot.assert_not_called()

    async def test_schema_coerces_numeric_strings(self):
        seen: dict = {}

        async def _echo(args, ctx):
            seen.update(args)
            return {"echo": args}

        self.registry.register(AgentTool(
            name="echo",
            description="test echo",
            input_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
            handler=_echo,
        ))
        res = await self.registry.execute("echo", {"n": "5"}, context=self.ctx)
        self.assertTrue(res["ok"])
        self.assertEqual(seen["n"], 5)

    async def test_read_tool_executes(self):
        res = await self.registry.execute("list_bots", {}, context=self.ctx)
        self.assertTrue(res["ok"])
        self.assertEqual(res["result"], {"bots": [], "count": 0})

    async def test_help_tool_executes(self):
        res = await self.registry.execute("help", {}, context=self.ctx)
        self.assertTrue(res["ok"])
        self.assertIn("TRADE_COPILOT", res["result"]["text"])

    async def test_control_tool_refused_without_confirm(self):
        res = await self.registry.execute("stop_all_bots", {}, confirmed=False, context=self.ctx)
        self.assertFalse(res["ok"])
        self.assertTrue(res["requires_confirmation"])
        self.assertEqual(res["pending"], {"type": "stop_all_bots", "params": {}})
        self.state.bot_manager.stop_all_bots.assert_not_called()

    async def test_control_tool_executes_with_confirm(self):
        res = await self.registry.execute(
            "pause_bot", {"bot_id": "bot-1"}, confirmed=True, context=self.ctx
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["result"], {"bot_id": "bot-1", "action": "pause_bot"})
        self.state.bot_manager.pause_bot.assert_awaited_once_with("bot-1")

    async def test_control_plan_error_when_bot_unresolvable(self):
        res = await self.registry.execute("pause_bot", {}, confirmed=False, context=self.ctx)
        self.assertFalse(res["ok"])
        self.assertNotIn("requires_confirmation", res)
        self.assertIn("Specify a bot id or symbol", res["error"])

    async def test_deploy_plan_builds_pending_params(self):
        res = await self.registry.execute(
            "deploy_bot",
            {"symbol": "ETHUSDT", "strategy": "BRS_SCALPING", "allocation": 2000},
            confirmed=False,
            context=self.ctx,
        )
        self.assertFalse(res["ok"])
        self.assertTrue(res["requires_confirmation"])
        pending = res["pending"]
        self.assertEqual(pending["type"], "deploy_bot")
        self.assertEqual(pending["params"]["symbol"], "ETHUSDT")
        self.assertEqual(pending["params"]["strategy"], "BRS_SCALPING")
        self.assertEqual(pending["params"]["allocation"], 2000.0)
        self.assertEqual(pending["params"]["config"], {"pipeline_source": "copilot"})
        self.state.bot_manager.create_bot.assert_not_called()


class CopilotRegistryRoutingTests(unittest.IsolatedAsyncioTestCase):
    """Copilot agent path must execute through the registry and keep HITL flow."""

    def setUp(self):
        self.state = _make_state()

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", True)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    @patch("app.services.agent.copilot_agent.plan_tool_calls", new_callable=AsyncMock)
    async def test_mutating_tool_routes_to_pending_then_confirm(self, mock_plan):
        mock_plan.return_value = {
            "tool_calls": [{
                "name": "deploy_bot",
                "arguments": {"symbol": "ETHUSDT", "strategy": "CHART_AGENT", "allocation": 2000},
            }],
            "direct_reply": None,
        }
        res = await handle_message(
            self.state,
            "deploy chart agent on ethusdt with $2000",
            session_id="agent-tools-deploy",
        )
        self.assertTrue(res.ok)
        self.assertTrue(res.requires_confirmation)
        self.assertIsNotNone(res.pending_id)
        self.assertEqual(res.pending_action["type"], "deploy_bot")
        self.assertEqual(res.pending_action["params"]["symbol"], "ETHUSDT")
        self.assertEqual(res.pending_action["params"]["allocation"], 2000.0)
        self.state.bot_manager.create_bot.assert_not_called()

        confirmed = await confirm_action(self.state, res.pending_id)
        self.assertTrue(confirmed["ok"])
        self.assertEqual(confirmed["result"]["bot_id"], "bot-xyz")
        self.state.bot_manager.create_bot.assert_awaited()
        self.assertIsNone(pop_pending(res.pending_id))

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", True)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    @patch("app.services.agent.copilot_agent.plan_tool_calls", new_callable=AsyncMock)
    async def test_read_tool_executes_through_registry(self, mock_plan):
        mock_plan.return_value = {
            "tool_calls": [{"name": "list_bots", "arguments": {}}],
            "direct_reply": None,
        }
        res = await handle_message(self.state, "show my bots", session_id="agent-tools-list")
        self.assertTrue(res.ok)
        tool = next(t for t in res.tool_results if t["tool"] == "list_bots")
        self.assertEqual(tool["result"], {"bots": [], "count": 0})
        self.assertFalse(res.requires_confirmation)

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", True)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    @patch("app.services.agent.copilot_agent.plan_tool_calls", new_callable=AsyncMock)
    async def test_unknown_tool_surfaces_error_result(self, mock_plan):
        mock_plan.return_value = {
            "tool_calls": [{"name": "fly_to_moon", "arguments": {}}],
            "direct_reply": None,
        }
        res = await handle_message(self.state, "fly to the moon", session_id="agent-tools-unknown")
        self.assertTrue(res.ok)
        tool = res.tool_results[0]
        self.assertEqual(tool["tool"], "fly_to_moon")
        self.assertIn("Unknown tool", tool["result"]["error"])


class AgentToolsHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from starlette.testclient import TestClient

        from app.api.http.app import create_http_app
        from app.api.state import AppState

        oms = MagicMock()
        bot_manager = MagicMock()
        bot_manager.active_bots = {}
        bot_manager.list_bots_public.return_value = []
        bot_manager.stop_all_bots = AsyncMock()
        manager = MagicMock()
        manager.connected_clients = set()
        state = AppState(
            oms=oms,
            manager=manager,
            bot_manager=bot_manager,
            backtester=None,
            chart_analyst=None,
        )
        cls.bot_manager = bot_manager
        cls.client = TestClient(create_http_app(state))

    def test_list_tools_endpoint(self):
        resp = self.client.get("/api/v1/agent/tools")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        names = {t["name"] for t in body["tools"]}
        self.assertEqual(names, EXPECTED_TOOLS)
        deploy = next(t for t in body["tools"] if t["name"] == "deploy_bot")
        self.assertEqual(deploy["side_effect"], "trade")
        self.assertTrue(deploy["hitl_required"])

    def test_call_read_tool(self):
        resp = self.client.post("/api/v1/agent/tools/call", json={"name": "list_bots", "args": {}})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["result"], {"bots": [], "count": 0})

    def test_call_control_tool_requires_confirmation(self):
        self.bot_manager.stop_all_bots.reset_mock()
        resp = self.client.post("/api/v1/agent/tools/call", json={"name": "stop_all_bots", "args": {}})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["requires_confirmation"])
        self.assertEqual(body["pending_action"]["type"], "stop_all_bots")
        self.assertTrue(body["pending_id"])
        self.bot_manager.stop_all_bots.assert_not_called()

    def test_call_control_tool_confirmed_executes(self):
        resp = self.client.post(
            "/api/v1/agent/tools/call",
            json={"name": "stop_all_bots", "args": {}, "confirmed": True},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["result"]["action"], "stop_all_bots")
        self.bot_manager.stop_all_bots.assert_awaited()

    def test_call_unknown_tool(self):
        resp = self.client.post("/api/v1/agent/tools/call", json={"name": "nope", "args": {}})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["ok"])


if __name__ == "__main__":
    unittest.main()
