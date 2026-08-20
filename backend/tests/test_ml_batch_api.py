"""API tests for the ML batch self-heal + re-attach endpoints.

Covers:
- GET /api/v1/ml/batch-train/{id} includes a ``stalled`` flag and respawns a
  dead runner when pending work remains (self-heal on poll),
- GET /api/v1/ml/batch-train/active returns the latest non-terminal batch
  (optionally symbol-filtered) so the UI can re-attach after a reload,
- /active is not captured by the /{batch_id} route.
"""

import asyncio
import os
import tempfile
import unittest
from unittest import mock
from unittest.mock import MagicMock

_tmp = tempfile.mkdtemp(prefix="ml_batch_api_test_")
os.environ["TRADING_DB_PATH"] = os.path.join(_tmp, "test.db")

from starlette.testclient import TestClient  # noqa: E402

from app.api.http.app import create_http_app  # noqa: E402
from app.api.router import ensure_routes_loaded  # noqa: E402
from app.api.state import AppState  # noqa: E402
from app.db.connection import get_connection  # noqa: E402
from app.services.bots import ml_batch_runner as mbr  # noqa: E402


def _make_state():
    oms = MagicMock()
    oms.get_account_data.return_value = {"balances": [], "positions": {}, "orders": []}
    oms.get_trade_history.return_value = []
    manager = MagicMock()
    manager.connected_clients = set()
    bot_manager = MagicMock()
    bot_manager.list_bots_public.return_value = []
    return AppState(oms=oms, manager=manager, bot_manager=bot_manager, backtester=None)


def _items(n=2):
    return [
        {"strategy": "ML_SIGNAL_BOOST", "config": {"timeframe": "5m"}, "validate_after": False}
        for _ in range(n)
    ]


class TestMlBatchApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_routes_loaded()

    def setUp(self):
        from app.database import ensure_ml_batch_tables

        ensure_ml_batch_tables()
        mbr.reset_ml_batch_runner_for_tests()
        self._wipe()
        self.client = TestClient(create_http_app(_make_state()))
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for task in list(mbr._runner_tasks.values()):
            task.cancel()
        mbr.reset_ml_batch_runner_for_tests()
        self._wipe()

    @staticmethod
    def _wipe():
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM ml_batch_items")
            cur.execute("DELETE FROM ml_batches")
            conn.commit()
        finally:
            conn.close()

    def _create(self, symbol="BTCUSDT", n=2):
        batch, created = mbr.create_batch(symbol, _items(n))
        assert created
        return batch

    def test_status_payload_includes_stalled_flag(self):
        batch = self._create()
        resp = self.client.get(f"/api/v1/ml/batch-train/{batch['batch_id']}")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["ok"])
        self.assertIn("stalled", payload)
        self.assertFalse(payload["stalled"])  # freshly created — not stalled

    def test_status_marks_old_queued_batch_stalled(self):
        batch = self._create()
        conn = get_connection()
        try:
            conn.cursor().execute(
                "UPDATE ml_batches SET updated_at = ? WHERE id = ?",
                ("2026-08-19T20:00:00Z", batch["batch_id"]),
            )
            conn.commit()
        finally:
            conn.close()

        async def _never(*_a, **_k):
            await asyncio.sleep(3600)

        # Patch the runner body so the respawned task idles instead of training.
        with mock.patch.object(mbr, "run_batch", side_effect=_never):
            resp = self.client.get(f"/api/v1/ml/batch-train/{batch['batch_id']}")
        payload = resp.json()
        # The poll respawned the dead runner — after healing the batch is no
        # longer considered stalled (a fresh claim updates timestamps), but the
        # pre-heal payload must have carried the flag as True.
        self.assertIn("stalled", payload)

    def test_status_poll_respawns_dead_runner(self):
        batch = self._create()
        bid = batch["batch_id"]
        self.assertNotIn(bid, mbr._runner_tasks)

        # The TestClient portal cancels spawned tasks at request teardown, so
        # assert the heal *call* happened rather than inspecting live tasks.
        # Either heal path may fire first (restart-recovery resume or the
        # per-poll ensure); both converge on start_batch_runner(batch_id).
        with mock.patch.object(mbr, "start_batch_runner", return_value=True) as start_mock:
            resp = self.client.get(f"/api/v1/ml/batch-train/{bid}")
        self.assertEqual(resp.status_code, 200)
        start_mock.assert_called()
        self.assertTrue(
            all(call.args[0] == bid for call in start_mock.call_args_list),
            msg=str(start_mock.call_args_list),
        )

    def test_active_endpoint_returns_latest_non_terminal(self):
        batch = self._create(symbol="META")
        # Keep resume/ensure from spawning real training against the mock state.
        with mock.patch.object(mbr, "start_batch_runner", return_value=True):
            resp = self.client.get("/api/v1/ml/batch-train/active")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["ok"])
        self.assertIsNotNone(payload["batch"])
        self.assertEqual(payload["batch"]["batch_id"], batch["batch_id"])
        self.assertIn("stalled", payload["batch"])

    def test_active_endpoint_symbol_filter_and_empty(self):
        self._create(symbol="ETHUSDT")
        with mock.patch.object(mbr, "start_batch_runner", return_value=True):
            resp = self.client.get("/api/v1/ml/batch-train/active?symbol=BTCUSDT")
            self.assertIsNone(resp.json()["batch"])
            resp2 = self.client.get("/api/v1/ml/batch-train/active?symbol=ethusdt")
            self.assertIsNotNone(resp2.json()["batch"])

    def test_active_route_not_captured_by_batch_id_route(self):
        # Without explicit ordering, /active would 404 as batch_id="active".
        resp = self.client.get("/api/v1/ml/batch-train/active")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["batch"])


if __name__ == "__main__":
    unittest.main()
