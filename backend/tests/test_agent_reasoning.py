"""Tests for durable agent reasoning chains (store + HTTP API)."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""
# Never touch profile DBs (trading-alpaca.db / trading.db) from unit tests.
os.environ.pop("TERMINAL_PROFILE", None)
_TEST_DIR = tempfile.mkdtemp()
os.environ["SQLITE_DB_PATH"] = os.path.join(_TEST_DIR, "agent_reasoning_test.db")

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.environ["SQLITE_DB_PATH"]
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
db_conn._pool = None  # drop any pool bound before path rebind
app_config.DB_PATH = db_conn.DB_PATH
assert os.path.basename(db_conn.DB_PATH).lower() not in {
    "trading-alpaca.db", "trading-ib.db", "trading-massive.db", "trading-sim.db", "trading.db",
}, db_conn.DB_PATH

from app.database import init_db  # noqa: E402
from app.services.agent.reasoning import AgentReasoning, Observation  # noqa: E402
from app.services.agent.reasoning_store import (  # noqa: E402
    RETAIN_PER_BOT,
    list_agent_reasoning,
    save_agent_reasoning,
)


def _make_reasoning(decision: str = "VETO", synthesis: str = "Gap too wide.") -> AgentReasoning:
    return AgentReasoning(
        observations=[
            Observation("market_anomaly", "danger", 0.95, "Price gap of 3.10%", {"gap_pct": 3.1}),
            Observation("sentiment", "neutral", 0.5, "Data missing."),
        ],
        synthesis=synthesis,
        decision=decision,
        confidence=0.9,
        recommendation_strength="strong",
    )


class TestAgentReasoningStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_save_and_list_roundtrip(self):
        reasoning = _make_reasoning()
        ok = save_agent_reasoning(
            "bot-rt", "PRETRADE_INTEL", reasoning,
            vetoes=["price_gap_anomaly: 3.10% gap"],
            size_multiplier=0.0,
            ts=1700000100.0,
        )
        self.assertTrue(ok)

        chains = list_agent_reasoning("bot-rt", limit=20)
        self.assertEqual(len(chains), 1)
        chain = chains[0]
        self.assertEqual(chain["bot_id"], "bot-rt")
        self.assertEqual(chain["agent"], "PRETRADE_INTEL")
        self.assertEqual(chain["verdict"], "VETO")
        self.assertEqual(chain["notes"], "Gap too wide.")
        self.assertEqual(chain["size_multiplier"], 0.0)
        self.assertEqual(chain["vetoes"], ["price_gap_anomaly: 3.10% gap"])
        self.assertEqual(len(chain["observations"]), 2)
        self.assertEqual(chain["observations"][0]["source"], "market_anomaly")
        self.assertEqual(chain["observations"][0]["data"]["gap_pct"], 3.1)
        self.assertEqual(chain["ts"], 1700000100.0)
        self.assertTrue(chain["created_at"])

    def test_save_accepts_to_dict_payload(self):
        ok = save_agent_reasoning(
            "bot-dict", "RISK_SENTINEL", _make_reasoning(decision="PAUSE").to_dict(),
            ts=1700000200.0,
        )
        self.assertTrue(ok)
        chains = list_agent_reasoning("bot-dict")
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["verdict"], "PAUSE")
        self.assertEqual(chains[0]["agent"], "RISK_SENTINEL")

    def test_newest_first_and_limit(self):
        for i in range(5):
            save_agent_reasoning(
                "bot-ord", "REGIME_ROTATION", _make_reasoning(decision="ROTATE"),
                ts=1700001000.0 + i,
            )
        chains = list_agent_reasoning("bot-ord", limit=3)
        self.assertEqual(len(chains), 3)
        self.assertEqual(chains[0]["ts"], 1700001004.0)
        self.assertEqual(chains[-1]["ts"], 1700001002.0)

    def test_retention_prunes_per_bot(self):
        for i in range(RETAIN_PER_BOT + 5):
            save_agent_reasoning(
                "bot-prune", "POSTTRADE_LEARNER",
                _make_reasoning(decision="LEARN_AND_ADJUST"),
                ts=1700010000.0 + i,
            )
        save_agent_reasoning("bot-other", "RISK_SENTINEL", _make_reasoning(), ts=1700020000.0)

        from app.database import get_connection

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM agent_reasoning WHERE bot_id = ?", ("bot-prune",))
            self.assertEqual(int(cur.fetchone()[0]), RETAIN_PER_BOT)
            cur.execute(
                "SELECT MIN(ts), MAX(ts) FROM agent_reasoning WHERE bot_id = ?",
                ("bot-prune",),
            )
            row = cur.fetchone()
            # Newest rows survive the prune.
            self.assertEqual(float(row[0]), 1700010005.0)
            self.assertEqual(float(row[1]), 1700010000.0 + RETAIN_PER_BOT + 4)
        finally:
            conn.close()
        # Other bots are untouched.
        self.assertEqual(len(list_agent_reasoning("bot-other")), 1)

    def test_save_is_best_effort(self):
        # Missing bot_id → clean no-op.
        self.assertFalse(save_agent_reasoning("", "RISK_SENTINEL", _make_reasoning()))
        self.assertFalse(save_agent_reasoning(None, "RISK_SENTINEL", _make_reasoning()))
        # Non-reasoning payloads degrade instead of raising.
        self.assertTrue(save_agent_reasoning("bot-junk", "X", object(), ts=1700030000.0))
        # DB failure must never propagate to the trading path.
        import app.services.agent.reasoning_store as store

        original = store.db_session
        def _boom(*_args, **_kwargs):
            raise RuntimeError("db down")
        store.db_session = _boom
        try:
            self.assertFalse(save_agent_reasoning("bot-x", "X", _make_reasoning()))
        finally:
            store.db_session = original


class TestAgentReasoningApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        from starlette.testclient import TestClient

        from app.api.http.app import create_http_app
        from app.api.state import AppState

        oms = MagicMock()
        bot_manager = MagicMock()
        manager = MagicMock()
        manager.connected_clients = set()
        state = AppState(oms=oms, manager=manager, bot_manager=bot_manager,
                         backtester=None, chart_analyst=None)
        cls.client = TestClient(create_http_app(state))

    def test_reasoning_endpoint_shape(self):
        save_agent_reasoning(
            "bot-api", "PRETRADE_INTEL", _make_reasoning(),
            vetoes=["event_policy_macro: CPI in 30m"],
            size_multiplier=0.0,
            ts=1700040000.0,
        )
        resp = self.client.get("/api/v1/bots/bot-api/reasoning?limit=5")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        chains = body["reasoning"]
        self.assertEqual(len(chains), 1)
        chain = chains[0]
        for key in (
            "id", "bot_id", "agent", "verdict", "notes", "observations",
            "vetoes", "size_multiplier", "ts", "created_at",
        ):
            self.assertIn(key, chain)
        self.assertEqual(chain["bot_id"], "bot-api")
        self.assertIsInstance(chain["observations"], list)
        self.assertIsInstance(chain["vetoes"], list)

    def test_reasoning_endpoint_empty_for_unknown_bot(self):
        resp = self.client.get("/api/v1/bots/bot-nope/reasoning")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["reasoning"], [])

    def test_reasoning_endpoint_bad_limit_falls_back(self):
        resp = self.client.get("/api/v1/bots/bot-api/reasoning?limit=abc")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])


if __name__ == "__main__":
    unittest.main()
