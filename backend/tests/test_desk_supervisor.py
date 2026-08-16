"""Tests for the Desk Supervisor (AUTO_AGENT_ACTIONS gate + actor integration)."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""
os.environ.pop("TERMINAL_PROFILE", None)
_TEST_DIR = tempfile.mkdtemp()
os.environ["SQLITE_DB_PATH"] = os.path.join(_TEST_DIR, "desk_supervisor_test.db")

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.environ["SQLITE_DB_PATH"]
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
db_conn._pool = None
app_config.DB_PATH = db_conn.DB_PATH
assert os.path.basename(db_conn.DB_PATH).lower() not in {
    "trading-alpaca.db", "trading-ib.db", "trading-massive.db", "trading-sim.db", "trading.db",
}, db_conn.DB_PATH

from app.database import init_db  # noqa: E402
from app.services.agent import action_queue, desk_supervisor  # noqa: E402


def _set_auto(value: bool):
    app_config.AUTO_AGENT_ACTIONS = value


class TestDeskSupervisor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        action_queue.clear_callbacks()
        action_queue.set_event_bus(None)
        action_queue.set_state_provider(None)
        from app.db.connection import db_session

        with db_session() as conn:
            conn.cursor().execute("DELETE FROM agent_pending_actions")
        self._orig = app_config.AUTO_AGENT_ACTIONS

    def tearDown(self):
        app_config.AUTO_AGENT_ACTIONS = self._orig

    def test_flag_on_executes_immediately(self):
        _set_auto(True)
        calls = []

        async def _exec():
            calls.append(1)
            return "done"

        out = asyncio.run(
            desk_supervisor.propose_or_execute("RiskSentinel", "pause_bot", {}, "r", _exec)
        )
        self.assertTrue(out.get("executed"))
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("result"), "done")
        self.assertEqual(calls, [1])
        self.assertEqual(action_queue.list_actions("pending"), [])

    def test_flag_off_parks_non_emergency(self):
        _set_auto(False)
        calls = []

        async def _exec():
            calls.append(1)

        out = asyncio.run(
            desk_supervisor.propose_or_execute(
                "RegimeRotation", "rotate_strategy", {"bot_id": "b-1"}, "regime shift", _exec
            )
        )
        self.assertTrue(out.get("pending"))
        self.assertIsInstance(out.get("action_id"), int)
        self.assertEqual(calls, [])

        pending = action_queue.list_actions("pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["actor"], "RegimeRotation")
        self.assertEqual(pending[0]["action_type"], "rotate_strategy")

    def test_emergency_bypasses_queue_when_flag_off(self):
        _set_auto(False)
        calls = []

        async def _exec():
            calls.append(1)
            return {"stopped": 4}

        out = asyncio.run(
            desk_supervisor.propose_or_execute(
                "RiskMonitor", "stop_all_bots", {}, "drawdown kill switch", _exec,
                emergency=True,
            )
        )
        self.assertTrue(out.get("executed"))
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("result"), {"stopped": 4})
        self.assertEqual(calls, [1])
        self.assertEqual(action_queue.list_actions("pending"), [])

    def test_immediate_execution_error_is_reported(self):
        _set_auto(True)

        def _boom():
            raise RuntimeError("pause failed")

        out = asyncio.run(
            desk_supervisor.propose_or_execute("RiskSentinel", "pause_bot", {}, "r", _boom)
        )
        self.assertTrue(out.get("executed"))
        self.assertFalse(out.get("ok"))
        self.assertIn("pause failed", out.get("error") or "")

    def test_queue_failure_falls_back_to_direct_execution(self):
        _set_auto(False)
        calls = []

        async def _exec():
            calls.append(1)

        original = action_queue.propose_action

        def _fail(*_a, **_kw):
            raise RuntimeError("db down")

        action_queue.propose_action = _fail
        try:
            out = asyncio.run(
                desk_supervisor.propose_or_execute("AlphaDecay", "pause_bot", {}, "r", _exec)
            )
        finally:
            action_queue.propose_action = original
        self.assertTrue(out.get("executed"))
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("fallback"))
        self.assertEqual(calls, [1])

    def test_supervisor_instance_facade(self):
        _set_auto(True)
        sup = desk_supervisor.get_supervisor()
        out = asyncio.run(
            sup.propose_or_execute("X", "read_thing", {}, "r", lambda: 42)
        )
        self.assertEqual(out.get("result"), 42)
        self.assertIs(desk_supervisor.get_supervisor(), sup)


class TestSentinelIntegration(unittest.TestCase):
    """RiskSentinel streak pause parks in queue when AUTO_AGENT_ACTIONS=false."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        action_queue.clear_callbacks()
        action_queue.set_event_bus(None)
        from app.db.connection import db_session

        with db_session() as conn:
            conn.cursor().execute("DELETE FROM agent_pending_actions")
        self._orig = app_config.AUTO_AGENT_ACTIONS

    def tearDown(self):
        app_config.AUTO_AGENT_ACTIONS = self._orig

    def _make_sentinel_fixture(self):
        from app.services.bots.risk_sentinel import RiskSentinel

        bot_manager = MagicMock()
        bot_manager.pause_bot = AsyncMock()
        bot_manager.log_bot_event = AsyncMock()
        bot_manager.active_bots = {
            "bot-1": {
                "id": "bot-1",
                "status": "RUNNING",
                "symbol": "BTCUSDT",
                "config": {"max_consecutive_losses": 3},
            }
        }
        snapshot = MagicMock()
        snapshot.current_drawdown_pct = 0.0
        snapshot.account_equity = 10000.0
        oms = MagicMock()
        return RiskSentinel(agent_event_bus=None), snapshot, oms, bot_manager

    def test_sentinel_pause_parked_when_flag_off(self):
        _set_auto(False)
        sentinel, snapshot, oms, bot_manager = self._make_sentinel_fixture()

        import app.services.bots.analytics as analytics

        orig_streak = analytics.get_recent_consecutive_losses
        analytics.get_recent_consecutive_losses = lambda bot_id: 5
        try:
            results = asyncio.run(sentinel.evaluate(snapshot, oms, bot_manager))
        finally:
            analytics.get_recent_consecutive_losses = orig_streak

        # Parked — not executed.
        bot_manager.pause_bot.assert_not_called()
        self.assertEqual(results["streak_paused_count"], 0)
        self.assertEqual(len(results.get("proposed_actions") or []), 1)

        pending = action_queue.list_actions("pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["actor"], "RiskSentinel")
        self.assertEqual(pending[0]["action_type"], "pause_bot")
        self.assertEqual(pending[0]["params"]["bot_id"], "bot-1")

    def test_sentinel_pause_executes_when_flag_on(self):
        _set_auto(True)
        sentinel, snapshot, oms, bot_manager = self._make_sentinel_fixture()

        import app.services.bots.analytics as analytics

        orig_streak = analytics.get_recent_consecutive_losses
        analytics.get_recent_consecutive_losses = lambda bot_id: 5
        try:
            results = asyncio.run(sentinel.evaluate(snapshot, oms, bot_manager))
        finally:
            analytics.get_recent_consecutive_losses = orig_streak

        bot_manager.pause_bot.assert_awaited_once_with("bot-1")
        self.assertEqual(results["streak_paused_count"], 1)
        self.assertEqual(action_queue.list_actions("pending"), [])

    def test_sentinel_parked_action_approval_executes_pause(self):
        _set_auto(False)
        sentinel, snapshot, oms, bot_manager = self._make_sentinel_fixture()

        import app.services.bots.analytics as analytics

        orig_streak = analytics.get_recent_consecutive_losses
        analytics.get_recent_consecutive_losses = lambda bot_id: 5
        try:
            asyncio.run(sentinel.evaluate(snapshot, oms, bot_manager))
        finally:
            analytics.get_recent_consecutive_losses = orig_streak

        pending = action_queue.list_actions("pending")
        self.assertEqual(len(pending), 1)
        # pause_bot IS a registry tool — approval routes through the registry,
        # which needs a state with a bot_manager carrying an async pause_bot.
        state = MagicMock()
        state.bot_manager = bot_manager
        action_queue.set_state_provider(lambda: state)

        res = asyncio.run(action_queue.approve_action(pending[0]["id"]))
        self.assertTrue(res.get("ok"), res)
        bot_manager.pause_bot.assert_awaited()


if __name__ == "__main__":
    unittest.main()
