"""HTTP analytics endpoint tests."""

import os
import tempfile
import unittest

import app.db.connection as db_conn
from app.api.http.dispatch import invoke_action, http_status_and_body
from app.api.state import AppState
from app.database import get_connection, init_db


class HttpAnalyticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.mkdtemp()
        db_conn.DB_PATH = os.path.join(self._tmpdir, "http_analytics.db")
        db_conn._pool = None
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO orders (id, symbol, type, side, price, quantity, status, filled_quantity, average_fill_price) "
            "VALUES ('t1', 'BTCUSDT', 'MARKET', 'SELL', 100, 1, 'FILLED', 1, 105)"
        )
        conn.commit()
        conn.close()

        from unittest.mock import MagicMock
        from app.services.sim_oms import SimulatedOMSService

        oms = SimulatedOMSService(feed=MagicMock())
        manager = MagicMock()
        bot_manager = MagicMock()
        self.state = AppState(
            oms=oms,
            manager=manager,
            bot_manager=bot_manager,
            backtester=None,
            chart_analyst=None,
        )

    async def test_analytics_dashboard_http(self):
        messages = await invoke_action(
            self.state,
            "analytics_get",
            {"report": "dashboard", "period": "ALL", "source": "combined"},
        )
        status, body = http_status_and_body(messages)
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("type"), "analytics_report")
        data = body.get("data") or {}
        self.assertEqual(data.get("report"), "dashboard")
        self.assertIn("equity", data)
        self.assertIn("risk", data)


if __name__ == "__main__":
    unittest.main()
