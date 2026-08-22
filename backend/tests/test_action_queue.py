"""Tests for the HITL desk action queue (propose/approve/reject/expire)."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""
os.environ.pop("TERMINAL_PROFILE", None)
_TEST_DIR = tempfile.mkdtemp()
os.environ["SQLITE_DB_PATH"] = os.path.join(_TEST_DIR, "agent_action_queue_test.db")

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
from app.services.agent import action_queue  # noqa: E402


class TestActionQueue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        action_queue.clear_callbacks()
        action_queue.set_event_bus(None)
        action_queue.set_state_provider(None)
        # Clean slate per test.
        from app.db.connection import db_session

        with db_session() as conn:
            conn.cursor().execute("DELETE FROM agent_pending_actions")

    def test_propose_parks_pending_action(self):
        out = action_queue.propose_action(
            "RiskSentinel", "pause_bot", {"bot_id": "b-1"}, "loss streak 5/5"
        )
        self.assertTrue(out.get("pending"))
        action_id = out.get("action_id")
        self.assertIsInstance(action_id, int)

        row = action_queue.get_action(action_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["actor"], "RiskSentinel")
        self.assertEqual(row["action_type"], "pause_bot")
        self.assertEqual(row["params"], {"bot_id": "b-1"})
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["reason"], "loss streak 5/5")

    def test_list_actions_filters_by_status(self):
        action_queue.propose_action("AlphaDecay", "pause_bot", {"bot_id": "b-2"}, "decay")
        action_queue.propose_action("RegimeRotation", "rotate_strategy", {"bot_id": "b-3"}, "regime")
        pending = action_queue.list_actions("pending")
        self.assertEqual(len(pending), 2)
        # Newest first.
        self.assertEqual(pending[0]["actor"], "RegimeRotation")
        approved = action_queue.list_actions("approved")
        self.assertEqual(approved, [])
        # Unknown status → unfiltered list.
        all_rows = action_queue.list_actions("bogus")
        self.assertEqual(len(all_rows), 2)

    def test_approve_executes_actor_callback(self):
        calls = []

        async def _exec():
            calls.append("ran")
            return {"paused": True}

        out = action_queue.propose_action(
            "RiskSentinel", "custom_pause", {"bot_id": "b-4"}, "streak",
            execute_fn=_exec,
        )
        action_id = out["action_id"]
        res = asyncio.run(action_queue.approve_action(action_id))
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(calls, ["ran"])
        self.assertEqual(res["result"], {"paused": True})
        self.assertEqual(res["action"]["status"], "approved")
        self.assertIsNotNone(res["action"]["resolved_at"])

        row = action_queue.get_action(action_id)
        self.assertEqual(row["status"], "approved")

    def test_approve_without_executor_fails_cleanly(self):
        out = action_queue.propose_action(
            "ScannerDeploy", "no_such_tool_xyz", {"symbol": "BTCUSDT"}, "scan"
        )
        res = asyncio.run(action_queue.approve_action(out["action_id"]))
        self.assertFalse(res.get("ok"))
        self.assertIn("no executor", (res.get("error") or "").lower())
        # Still marked approved — the human decision stands, execution failed.
        self.assertEqual(action_queue.get_action(out["action_id"])["status"], "approved")

    def test_approve_twice_is_rejected(self):
        out = action_queue.propose_action("RiskSentinel", "pause_bot", {}, "r")
        action_id = out["action_id"]
        action_queue.reject_action(action_id)
        res = asyncio.run(action_queue.approve_action(action_id))
        self.assertFalse(res.get("ok"))
        self.assertIn("already rejected", res.get("error") or "")

    def test_reject_marks_status_and_drops_callback(self):
        called = []

        def _exec():
            called.append(1)

        out = action_queue.propose_action(
            "PostTradeLearner", "update_bot_config", {"bot_id": "b-5"}, "patch",
            execute_fn=_exec,
        )
        action_id = out["action_id"]
        res = action_queue.reject_action(action_id)
        self.assertTrue(res.get("ok"))
        self.assertEqual(res["action"]["status"], "rejected")
        self.assertEqual(called, [])

        # Rejecting again → already resolved error.
        res2 = action_queue.reject_action(action_id)
        self.assertFalse(res2.get("ok"))

    def test_reject_unknown_id(self):
        res = action_queue.reject_action(999999)
        self.assertFalse(res.get("ok"))
        self.assertIn("not found", (res.get("error") or "").lower())

    def test_expire_old_marks_stale_pending(self):
        out = action_queue.propose_action("AlphaDecay", "queue_retrain", {}, "stale")
        action_id = out["action_id"]

        # Fresh action survives expiry.
        self.assertEqual(action_queue.expire_old(ttl_sec=900), 0)
        self.assertEqual(action_queue.get_action(action_id)["status"], "pending")

        # Zero TTL → everything pending expires.
        expired = action_queue.expire_old(ttl_sec=0)
        self.assertEqual(expired, 1)
        self.assertEqual(action_queue.get_action(action_id)["status"], "expired")

        # list_actions triggers expiry inline too.
        out2 = action_queue.propose_action("AlphaDecay", "queue_retrain", {}, "stale2")
        action_queue.expire_old(ttl_sec=0)
        self.assertEqual(action_queue.get_action(out2["action_id"])["status"], "expired")

    def test_propose_publishes_event_when_bus_set(self):
        events = []

        class _Bus:
            async def publish(self, ev):
                events.append(ev)

            def _persist_event(self, ev):
                # No-running-loop fallback path in action_queue._publish_event.
                events.append(ev)

        action_queue.set_event_bus(_Bus())
        out = action_queue.propose_action("RiskSentinel", "pause_bot", {}, "r")
        types = [e.event_type for e in events]
        self.assertIn("AGENT_ACTION_PROPOSED", types)
        proposed = [e for e in events if e.event_type == "AGENT_ACTION_PROPOSED"][0]
        self.assertEqual(proposed.payload["action_id"], out["action_id"])
        self.assertEqual(proposed.payload["actor"], "RiskSentinel")

    def test_propose_same_bot_is_idempotent(self):
        first = action_queue.propose_action(
            "RiskSentinel", "pause_bot", {"bot_id": "b-dup", "symbol": "BTCUSDT"}, "streak"
        )
        second = action_queue.propose_action(
            "RiskSentinel", "pause_bot", {"bot_id": "b-dup", "symbol": "BTCUSDT"}, "streak again"
        )
        self.assertTrue(first.get("pending"))
        self.assertTrue(second.get("pending"))
        self.assertTrue(second.get("duplicate"))
        self.assertEqual(first["action_id"], second["action_id"])
        self.assertEqual(len(action_queue.list_actions("pending")), 1)

        other = action_queue.propose_action(
            "RiskSentinel", "pause_bot", {"bot_id": "b-other", "symbol": "ETHUSDT"}, "streak"
        )
        self.assertNotEqual(other["action_id"], first["action_id"])
        self.assertEqual(len(action_queue.list_actions("pending")), 2)

    def test_reject_cools_down_repropose(self):
        out = action_queue.propose_action(
            "RiskSentinel", "pause_bot", {"bot_id": "b-rej"}, "streak"
        )
        self.assertTrue(action_queue.reject_action(out["action_id"]).get("ok"))
        again = action_queue.propose_action(
            "RiskSentinel", "pause_bot", {"bot_id": "b-rej"}, "streak"
        )
        self.assertFalse(again.get("pending"))
        self.assertEqual(again.get("skipped"), "recently_rejected")
        self.assertEqual(action_queue.list_actions("pending"), [])

    def test_approve_expires_duplicate_siblings(self):
        from app.db.connection import db_session

        first = action_queue.propose_action(
            "RiskSentinel", "pause_bot", {"bot_id": "b-sib"}, "a"
        )
        # Bypass propose() dedupe to simulate historical clones already in DB.
        with db_session() as conn:
            conn.cursor().execute(
                """
                INSERT INTO agent_pending_actions
                    (actor, action_type, params_json, reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("RiskSentinel", "pause_bot", '{"bot_id": "b-sib"}', "clone", "pending", 1.0),
            )
        self.assertGreaterEqual(len(action_queue.list_actions("pending")), 1)
        # list_actions collapses clones; re-insert one sibling then approve the original.
        with db_session() as conn:
            conn.cursor().execute(
                """
                INSERT INTO agent_pending_actions
                    (actor, action_type, params_json, reason, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("RiskSentinel", "pause_bot", '{"bot_id": "b-sib"}', "clone2", "pending", 2.0),
            )
        asyncio.run(action_queue.approve_action(first["action_id"]))
        pending = action_queue.list_actions("pending")
        self.assertTrue(
            all((r.get("params") or {}).get("bot_id") != "b-sib" for r in pending),
            pending,
        )
        row = action_queue.get_action(first["action_id"])
        self.assertEqual(row["status"], "approved")


class TestActionQueueHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        from unittest.mock import AsyncMock, MagicMock
        from starlette.testclient import TestClient

        from app.api.http.app import create_http_app
        from app.api.state import AppState

        oms = MagicMock()
        bot_manager = MagicMock()
        bot_manager.pause_bot = AsyncMock()
        bot_manager.stop_bot = AsyncMock()
        manager = MagicMock()
        manager.connected_clients = set()
        state = AppState(oms=oms, manager=manager, bot_manager=bot_manager,
                         backtester=None, chart_analyst=None)
        cls.bot_manager = bot_manager
        cls.client = TestClient(create_http_app(state))

    def setUp(self):
        action_queue.clear_callbacks()
        from app.db.connection import db_session

        with db_session() as conn:
            conn.cursor().execute("DELETE FROM agent_pending_actions")

    def test_list_endpoint(self):
        action_queue.propose_action("RiskSentinel", "pause_bot", {"bot_id": "b-http"}, "streak")
        resp = self.client.get("/api/v1/agent/actions?status=pending")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["actions"]), 1)
        row = body["actions"][0]
        self.assertEqual(row["actor"], "RiskSentinel")
        self.assertEqual(row["params"]["bot_id"], "b-http")

    def test_approve_and_reject_endpoints(self):
        out = action_queue.propose_action(
            "RiskSentinel", "pause_bot", {"bot_id": "b-http2"}, "streak",
            execute_fn=lambda: {"ok": "paused"},
        )
        resp = self.client.post(f"/api/v1/agent/actions/{out['action_id']}/approve")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"], body)

        out2 = action_queue.propose_action("AlphaDecay", "queue_retrain", {}, "r")
        resp2 = self.client.post(f"/api/v1/agent/actions/{out2['action_id']}/reject")
        self.assertEqual(resp2.status_code, 200)
        self.assertTrue(resp2.json()["ok"])

        resp3 = self.client.post("/api/v1/agent/actions/not-a-number/approve")
        self.assertEqual(resp3.status_code, 400)


if __name__ == "__main__":
    unittest.main()
