"""WebSocket client disconnect metrics."""

from __future__ import annotations

import unittest

from app.observability.metrics import counter_sum, render_prometheus
from app.observability.ws_metrics import (
    record_ws_connect,
    record_ws_disconnect,
    ws_metrics_snapshot,
)


class TestWsMetrics(unittest.TestCase):
    def test_disconnect_by_code_and_prometheus(self):
        before = ws_metrics_snapshot()
        before_prom = counter_sum("ws_client_disconnects_total")

        record_ws_connect()
        record_ws_disconnect(1011, "keepalive ping timeout")
        record_ws_disconnect(1006, "")
        record_ws_disconnect(None, "abrupt")

        snap = ws_metrics_snapshot(connected=2)
        self.assertEqual(snap["connects_total"] - before["connects_total"], 1)
        self.assertEqual(snap["disconnects_total"] - before["disconnects_total"], 3)
        self.assertEqual(snap["connected"], 2)
        self.assertEqual(
            int(snap["by_code"].get("1011", 0)) - int(before["by_code"].get("1011", 0)),
            1,
        )
        self.assertEqual(
            int(snap["by_code"].get("1006", 0)) - int(before["by_code"].get("1006", 0)),
            2,
        )
        self.assertEqual(snap["last_disconnect"]["code"], 1006)
        self.assertGreaterEqual(len(snap["recent"]), 3)

        prom = render_prometheus()
        self.assertIn("ws_client_connects_total", prom)
        self.assertIn('ws_client_disconnects_total{category="timeout",code="1011"}', prom)
        self.assertGreaterEqual(
            counter_sum("ws_client_disconnects_total") - before_prom,
            3.0,
        )


if __name__ == "__main__":
    unittest.main()
