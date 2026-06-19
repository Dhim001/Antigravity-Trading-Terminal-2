"""Tests for bot log signal metadata (A2)."""

import json
import unittest

from app.api.outbound import bot_log


class TestBotLogMeta(unittest.TestCase):
    def test_bot_log_frame_includes_meta(self):
        meta = {
            "event_type": "signal",
            "bar_time": 1700000000,
            "signal_id": "bot1:1700000000:BUY",
            "symbol": "BTCUSDT",
            "timeframe": "5m",
        }
        frame = bot_log("bot-1", "INFO", "Entry BUY signal @ 100.00, qty 0.01", meta=meta)
        data = frame["data"]
        self.assertEqual(data["bot_id"], "bot-1")
        self.assertEqual(data["meta"]["bar_time"], 1700000000)
        self.assertEqual(data["meta"]["event_type"], "signal")

    def test_bot_log_frame_omits_meta_when_none(self):
        frame = bot_log("bot-1", "INFO", "Bot paused.")
        self.assertNotIn("meta", frame["data"])

    def test_meta_json_roundtrip(self):
        meta = {"event_type": "signal", "bar_time": 123}
        raw = json.dumps(meta)
        parsed = json.loads(raw)
        self.assertEqual(parsed["bar_time"], 123)


if __name__ == "__main__":
    unittest.main()
