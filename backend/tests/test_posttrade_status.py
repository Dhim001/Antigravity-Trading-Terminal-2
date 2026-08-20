"""Tests for the aggregated post-trade learning status endpoint service."""

import os
import tempfile
import unittest
from unittest import mock

_tmp = tempfile.mkdtemp(prefix="ptl_status_test_")
os.environ["TRADING_DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["DATA_DIR"] = _tmp

from app.services.bots.posttrade_status import get_posttrade_learning_status  # noqa: E402

_ROUTER_STATUS = {"trained": False, "labels": [], "training_pairs": 0}


def _status(manager):
    # Hermetic: the real copilot_intent data dir may be polluted by other tests.
    with mock.patch(
        "app.services.agent.copilot_intent_lora.intent_router_status",
        return_value=dict(_ROUTER_STATUS),
    ):
        return get_posttrade_learning_status(manager)


class _FakeManager:
    def __init__(self, bots):
        self.active_bots = bots


class TestPostTradeStatus(unittest.TestCase):
    def test_empty_fleet(self):
        res = _status(_FakeManager({}))
        self.assertTrue(res["ok"])
        self.assertEqual(res["bots"], [])
        self.assertIn("copilot_intent_router", res)
        self.assertFalse(res["copilot_intent_router"]["trained"])

    def test_bot_row_shape(self):
        bots = {
            "bot-1": {"symbol": "BTCUSDT", "strategy": "ML_SIGNAL_BOOST", "status": "RUNNING"},
        }
        res = _status(_FakeManager(bots))
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["bots"]), 1)
        row = res["bots"][0]
        self.assertEqual(row["symbol"], "BTCUSDT")
        # All subsystem keys present even with no artifacts on disk.
        for key in (
            "conformal",
            "regime_warning",
            "stacking",
            "isotonic_calibrated",
            "rl_replay_transitions",
            "posttrade_labels",
        ):
            self.assertIn(key, row)
        self.assertIsNone(row["conformal"])
        self.assertIsNone(row["stacking"])
        self.assertFalse(row["regime_warning"])
        self.assertFalse(row["isotonic_calibrated"])
        self.assertEqual(row["rl_replay_transitions"], 0)
        self.assertEqual(row["posttrade_labels"], 0)

    def test_never_raises_on_bad_manager(self):
        res = _status(object())
        self.assertTrue(res["ok"])
        self.assertEqual(res["bots"], [])


if __name__ == "__main__":
    unittest.main()
