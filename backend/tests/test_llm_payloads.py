"""Slim LLM payloads and template fallbacks."""

from __future__ import annotations

import json
import unittest

from app.services.agent.llm.base import finalize_narrative, parse_narrative_field
from app.services.agent.llm.payloads import (
    dumps_payload,
    slim_backtest_trade_payload,
    slim_insight_payload,
    template_backtest_narrative,
    template_insight_narrative,
)


class TestSlimPayloads(unittest.TestCase):
    def test_slim_insight_drops_llm_fields(self):
        raw = {
            "symbol": "AAPL",
            "signal": "BUY",
            "confidence": 0.72,
            "reasons": ["RSI cross", "Trend up", "Volume", "Extra"],
            "narrative": "Old LLM text",
            "model": "gemma3:4b",
            "insight_id": "AAPL:1m:123",
        }
        slim = slim_insight_payload(raw)
        self.assertEqual(slim["symbol"], "AAPL")
        self.assertEqual(len(slim["reasons"]), 3)
        self.assertNotIn("narrative", slim)
        self.assertNotIn("model", slim)
        self.assertNotIn("insight_id", slim)

    def test_dumps_payload_is_compact_json(self):
        payload = {"symbol": "BTCUSDT", "signal": "SELL"}
        text = dumps_payload(payload)
        self.assertEqual(json.loads(text), payload)
        self.assertNotIn(" ", text)

    def test_slim_backtest_bundle(self):
        bundle = {
            "symbol": "AAPL",
            "run_scope": "Walk-forward OOS",
            "trade_context": {"side": "BUY", "reason": "ENTRY", "bar_time_iso": "2024-01-01 12:00 UTC"},
            "insight": {"symbol": "AAPL", "signal": "BUY", "narrative": "skip me", "reasons": ["a", "b"]},
        }
        slim = slim_backtest_trade_payload(bundle)
        self.assertEqual(slim["trade_context"]["side"], "BUY")
        self.assertNotIn("narrative", slim["insight"])


class TestTemplateFallbacks(unittest.TestCase):
    def test_template_insight(self):
        text = template_insight_narrative({
            "symbol": "AAPL",
            "signal": "BUY",
            "confidence": 0.65,
            "reasons": ["RSI crossed above 50"],
            "timeframe": "5m",
        })
        self.assertIn("BUY", text or "")
        self.assertIn("AAPL", text or "")

    def test_template_backtest(self):
        text = template_backtest_narrative({
            "symbol": "AAPL",
            "run_scope": "Standard backtest",
            "trade_context": {"side": "BUY", "reason": "MACD cross", "bar_time_iso": "2024-06-01 09:30 UTC"},
        })
        self.assertIn("BUY entry", text or "")
        self.assertIn("MACD cross", text or "")


class TestNarrativeJsonParse(unittest.TestCase):
    def test_parse_json_explanation(self):
        raw = '{"explanation": "BUY on momentum breakout at bar close."}'
        self.assertEqual(
            parse_narrative_field(raw),
            "BUY on momentum breakout at bar close.",
        )

    def test_finalize_uses_fallback_when_empty(self):
        self.assertEqual(
            finalize_narrative(None, "BUY on AAPL at 65% confidence."),
            "BUY on AAPL at 65% confidence.",
        )


if __name__ == "__main__":
    unittest.main()
