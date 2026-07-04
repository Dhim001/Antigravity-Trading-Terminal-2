"""Tests for backtest explainability — blocked events and payload caps."""

import unittest

from app.services.bots.backtest_payload import (
    MAX_PERSIST_BLOCKED_EVENTS,
    MAX_WIRE_BLOCKED_EVENTS,
    trim_results_for_persist,
    trim_results_for_wire,
)
from app.services.bots.backtester import MAX_BLOCKED_EVENTS, _append_blocked_event


class TestBlockedEvents(unittest.TestCase):
    def test_append_blocked_event_caps_at_max(self):
        events: list[dict] = []
        total = [0]
        for i in range(MAX_BLOCKED_EVENTS + 25):
            _append_blocked_event(
                events,
                total_counter=total,
                kind="filter",
                reason=f"reject {i}",
                bar_time=1000 + i,
                bucket="trend",
            )
        self.assertEqual(len(events), MAX_BLOCKED_EVENTS)
        self.assertEqual(total[0], MAX_BLOCKED_EVENTS + 25)

    def test_wire_trim_blocked_events(self):
        events = [{"time": i, "kind": "filter", "reason": f"r{i}"} for i in range(80)]
        raw = {
            "summary": {
                "blocked_events": events,
                "blocked_events_total": 80,
                "blocked_events_truncated": False,
            },
        }
        out = trim_results_for_wire(raw)
        self.assertEqual(len(out["summary"]["blocked_events"]), MAX_WIRE_BLOCKED_EVENTS)
        self.assertTrue(out["summary"]["blocked_events_truncated"])
        self.assertEqual(out["summary"]["blocked_events_total"], 80)

    def test_persist_keeps_insight_snapshot(self):
        raw = {
            "trades": [{"pnl": 1, "insight_snapshot": {"signal": "BUY", "confidence": 0.7}}],
        }
        out = trim_results_for_persist(raw)
        self.assertIn("insight_snapshot", out["trades"][0])

    def test_persist_trim_blocked_events(self):
        events = [{"time": i, "kind": "risk_gate", "reason": "blocked"} for i in range(250)]
        raw = {"summary": {"blocked_events": events, "blocked_events_total": 250}}
        out = trim_results_for_persist(raw)
        self.assertEqual(len(out["summary"]["blocked_events"]), MAX_PERSIST_BLOCKED_EVENTS)
        self.assertTrue(out["summary"]["blocked_events_truncated"])


if __name__ == "__main__":
    unittest.main()
