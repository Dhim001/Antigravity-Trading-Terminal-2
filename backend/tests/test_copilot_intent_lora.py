"""Tests for P3 #14 — LoRA embedding fine-tune for copilot intent routing."""

import os
import tempfile
import unittest
from unittest import mock

_tmp = tempfile.mkdtemp(prefix="p3_lora_test_")
os.environ["TRADING_DB_PATH"] = os.path.join(_tmp, "test.db")

from app.services.agent import copilot_intent_lora as lora  # noqa: E402

# Hermetic data dir: app.config may already be imported (and DATA_DIR bound to
# the real path) by the time this module loads, so patch the module's path
# helpers directly instead of relying on the DATA_DIR env var.
_LORA_TMP = tempfile.mkdtemp(prefix="p3_lora_data_")


def _reset():
    for path in (lora._training_log_path(), lora._adapter_path(), lora._meta_path()):
        if os.path.isfile(path):
            os.remove(path)
    lora._invalidate_router_cache()


class _LoraTestCase(unittest.TestCase):
    """Base: patch the module's storage paths into a hermetic temp dir."""

    def setUp(self):
        patcher = mock.patch.multiple(
            lora,
            _data_dir=lambda: _LORA_TMP,
            _training_log_path=lambda: os.path.join(_LORA_TMP, "training_pairs.jsonl"),
            _adapter_path=lambda: os.path.join(_LORA_TMP, "lora_adapter.npz"),
            _meta_path=lambda: os.path.join(_LORA_TMP, "lora_meta.json"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        _reset()


def _log_synthetic_pairs(n_per_class: int = 30):
    """Log clearly separable synthetic pairs for each intent."""
    samples = {
        "analysis": [
            "what is btcusdt doing now",
            "analyze solusdt market trend",
            "is ethusdt ranging or trending",
            "show me the btcusdt regime",
        ],
        "explain": [
            "why did my bot sell solusdt",
            "explain the conformal gate",
            "what happened on that losing trade",
            "why was my bot paused",
        ],
        "action": [
            "deploy a bot on btcusdt",
            "pause my solusdt bot",
            "close all my positions now",
            "resume the paused ethusdt bot",
        ],
        "help": [
            "help",
            "what can you do",
            "show me the commands",
            "how do i use this",
        ],
    }
    for i in range(n_per_class):
        for intent, queries in samples.items():
            q = queries[i % len(queries)]
            lora.log_copilot_turn(q, intent, "tool_hint")


class TestDataCollection(_LoraTestCase):
    def test_log_and_count(self):
        lora.log_copilot_turn("what is btc doing", "analysis", "analyze_symbol")
        lora.log_copilot_turn("deploy a bot", "action", "deploy_bot")
        self.assertEqual(lora.training_pair_count(), 2)

    def test_invalid_intent_skipped(self):
        lora.log_copilot_turn("hello", "not_an_intent", None)
        lora.log_copilot_turn("", "analysis", None)
        self.assertEqual(lora.training_pair_count(), 0)

    def test_min_samples_gate(self):
        _log_synthetic_pairs(5)  # 20 pairs, below default 1000
        rows = lora.load_training_pairs()
        self.assertEqual(rows, [])
        rows = lora.load_training_pairs(min_samples=10)
        self.assertEqual(len(rows), 20)

    def test_log_disabled(self):
        with mock.patch("app.config.COPILOT_LORA_LOG_ENABLED", False):
            lora.log_copilot_turn("what is btc doing", "analysis", None)
        self.assertEqual(lora.training_pair_count(), 0)


class TestTraining(_LoraTestCase):
    def test_insufficient_samples(self):
        _log_synthetic_pairs(2)
        res = lora.train_intent_lora(min_samples=100)
        self.assertFalse(res["ok"])
        self.assertIn("insufficient", res["error"])

    def test_train_and_persist(self):
        _log_synthetic_pairs(30)  # 120 pairs
        res = lora.train_intent_lora(min_samples=20, epochs=30, lr=0.5)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["sample_count"], 120)
        self.assertEqual(len(res["labels"]), 4)
        self.assertGreater(res["train_accuracy"], 0.5)
        self.assertTrue(os.path.isfile(lora._adapter_path()))
        self.assertTrue(os.path.isfile(lora._meta_path()))

    def test_requires_two_classes(self):
        for _ in range(30):
            lora.log_copilot_turn("help me", "help", "help")
        res = lora.train_intent_lora(min_samples=10)
        self.assertFalse(res["ok"])
        self.assertIn("≥2", res["error"])


class TestRouting(_LoraTestCase):
    def _train(self):
        _log_synthetic_pairs(30)
        res = lora.train_intent_lora(min_samples=20, epochs=300, lr=1.0)
        self.assertTrue(res["ok"], res.get("error"))

    def test_predict_after_training(self):
        self._train()
        routed = lora.predict_intent("why did my bot sell solusdt")
        self.assertIsNotNone(routed)
        intent, conf = routed
        self.assertEqual(intent, "explain")
        self.assertGreaterEqual(conf, 0.6)

    def test_predict_none_without_model(self):
        self.assertIsNone(lora.predict_intent("what is btc doing"))

    def test_predict_none_when_disabled(self):
        self._train()
        with mock.patch("app.config.COPILOT_LORA_ENABLED", False):
            self.assertIsNone(lora.predict_intent("why did my bot sell"))

    def test_low_confidence_returns_none(self):
        self._train()
        with mock.patch("app.config.COPILOT_LORA_MIN_CONFIDENCE", 0.999):
            # A query unlike any training sample should not clear 0.999.
            self.assertIsNone(lora.predict_intent("xyzzy foobar nonsense tokens"))

    def test_status(self):
        self._train()
        status = lora.intent_router_status()
        self.assertTrue(status["trained"])
        self.assertEqual(len(status["labels"]), 4)
        self.assertEqual(status["training_pairs"], 120)


if __name__ == "__main__":
    unittest.main()
