"""Model registry and narrative validation tests."""

from __future__ import annotations

import unittest

from app.services.agent.llm.model_registry import enrich_model_ids, lookup_model_meta
from app.services.agent.llm.validation import validate_narrative


class TestModelRegistry(unittest.TestCase):
    def test_exact_match(self):
        meta = lookup_model_meta("llama3.2:3b")
        self.assertEqual(meta["tier"], "narrator")
        self.assertTrue(meta["recommended"])

    def test_pattern_match(self):
        meta = lookup_model_meta("deepseek-r1:8b")
        self.assertEqual(meta["tier"], "deep")
        self.assertTrue(meta["reasoning_capable"])

    def test_enrich_model_ids(self):
        enriched = enrich_model_ids(["gemma3:4b", "unknown-model"])
        self.assertEqual(len(enriched), 2)
        self.assertEqual(enriched[0]["tier"], "narrator")


class TestNarrativeValidation(unittest.TestCase):
    def test_accepts_valid_buy(self):
        ok, reason = validate_narrative(
            "BUY on AAPL after RSI crossed above 50 with momentum support.",
            context={"signal": "BUY", "symbol": "AAPL"},
        )
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_rejects_missing_side(self):
        ok, reason = validate_narrative(
            "Momentum improved on the daily close with elevated volume.",
            context={"signal": "BUY"},
            require_side=True,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_side")

    def test_rejects_too_short(self):
        ok, reason = validate_narrative("BUY.", context={"signal": "BUY"})
        self.assertFalse(ok)
        self.assertEqual(reason, "too_short")

    def test_accepts_backtest_context(self):
        ok, _ = validate_narrative(
            "SELL entry on TSLA at bar close after bearish crossover.",
            context={"trade_context": {"side": "SELL"}},
        )
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
