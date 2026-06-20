"""Assistant message text extraction for thinking-capable LLM APIs."""

from __future__ import annotations

import unittest

from app.services.agent.llm.base import extract_assistant_text


class TestExtractAssistantText(unittest.TestCase):
    def test_content_preferred(self):
        msg = {"content": "Entry on RSI cross.", "reasoning": "Let me think..."}
        self.assertEqual(extract_assistant_text(msg), "Entry on RSI cross.")

    def test_reasoning_fallback_when_content_empty(self):
        msg = {"content": "", "reasoning": "BUY fill after momentum breakout at bar close."}
        self.assertEqual(
            extract_assistant_text(msg),
            "BUY fill after momentum breakout at bar close.",
        )

    def test_whitespace_content_treated_as_empty(self):
        msg = {"content": "   ", "reasoning": "Fallback narrative."}
        self.assertEqual(extract_assistant_text(msg), "Fallback narrative.")

    def test_none_when_all_empty(self):
        self.assertIsNone(extract_assistant_text({"content": "", "reasoning": ""}))
        self.assertIsNone(extract_assistant_text(None))


if __name__ == "__main__":
    unittest.main()
