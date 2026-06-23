"""Prometheus metrics helpers and bot counter instrumentation."""

from __future__ import annotations

import unittest

from app.observability.metrics import counter_sum, inc, observability_snapshot, observe


class TestMetricsObservability(unittest.TestCase):
    def test_bot_counters_aggregate(self):
        inc("bot_signals_total", labels={"strategy": "CHART_AGENT", "signal": "BUY"})
        inc("bot_orders_blocked_total", labels={"strategy": "CHART_AGENT", "reason": "filter"})
        self.assertGreaterEqual(counter_sum("bot_signals_total"), 1.0)
        self.assertGreaterEqual(counter_sum("bot_orders_blocked_total"), 1.0)

    def test_observability_snapshot_includes_histogram(self):
        observe("agent_analyze_duration_seconds", 0.05)
        observe("agent_analyze_duration_seconds", 0.12)
        snap = observability_snapshot()
        self.assertIn("agent_analyze_p99_sec", snap)
        self.assertIsNotNone(snap["agent_analyze_p99_sec"])


if __name__ == "__main__":
    unittest.main()
