"""Assistant message text extraction for thinking-capable LLM APIs."""

from __future__ import annotations

import unittest

from app.services.agent.llm.base import extract_assistant_text, strip_reasoning_process


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

    def test_strips_think_block_from_content(self):
        think_open = "<" + "think>"
        think_close = "<" + "/think>"
        msg = {
            "content": (
                f"{think_open}Let me check RSI and trend alignment first.{think_close}"
                "BUY signal with bullish momentum on the daily close."
            ),
        }
        self.assertEqual(
            extract_assistant_text(msg),
            "BUY signal with bullish momentum on the daily close.",
        )

    def test_reasoning_cot_prefers_outcome_tail(self):
        msg = {
            "content": "",
            "reasoning": (
                "Let me analyze step by step.\n\n"
                "First, RSI is rising.\n\n"
                "Final answer: Entry aligns with momentum breakout at bar close."
            ),
        }
        self.assertEqual(
            extract_assistant_text(msg),
            "Entry aligns with momentum breakout at bar close.",
        )


class TestStripReasoningProcess(unittest.TestCase):
    def test_final_label(self):
        raw = "Let me think...\n\nSummary: RSI crossed above 50 with trend support."
        self.assertEqual(
            strip_reasoning_process(raw),
            "RSI crossed above 50 with trend support.",
        )

    def test_trailing_paragraph_when_cot(self):
        raw = (
            "Let me walk through the indicators.\n\n"
            "RSI is neutral.\n\n"
            "BUY on momentum breakout with elevated volume."
        )
        self.assertEqual(
            strip_reasoning_process(raw),
            "BUY on momentum breakout with elevated volume.",
        )

    def test_strips_run_command_and_question(self):
        raw = (
            "Let me analyze this fill.\n"
            "Run: ollama run deepseek-r1:8b\n"
            "Would you like more detail on the indicators?\n\n"
            "BUY entry after RSI crossed above 50 with bullish momentum on the bar close."
        )
        self.assertEqual(
            strip_reasoning_process(raw),
            "BUY entry after RSI crossed above 50 with bullish momentum on the bar close.",
        )

    def test_strips_code_fence_and_prompt_echo(self):
        raw = (
            "Explain this single backtest entry fill:\n"
            "```bash\nollama run gemma3:4b\n```\n"
            "Looking at the JSON, step 1 is check signal.\n\n"
            "SELL fill aligned with bearish reversal at the OOS bar close."
        )
        self.assertEqual(
            strip_reasoning_process(raw),
            "SELL fill aligned with bearish reversal at the OOS bar close.",
        )

    def test_json_echo_returns_none(self):
        self.assertIsNone(strip_reasoning_process('{"symbol": "AAPL", "side": "BUY"}'))

    def test_strips_numbered_reasoning_and_blockquote(self):
        raw = (
            "[1] Analyze the JSON payload.\n"
            "> First I'll check RSI and trend alignment.\n"
            "My reasoning: walk through each indicator.\n\n"
            "BUY entry triggered by momentum breakout at the bar close."
        )
        self.assertEqual(
            strip_reasoning_process(raw),
            "BUY entry triggered by momentum breakout at the bar close.",
        )

    def test_strips_mixed_single_paragraph_cot(self):
        raw = (
            "Let me think about this. The user wants an explanation. "
            "SELL fill on bearish RSI divergence at the OOS validation bar."
        )
        self.assertEqual(
            strip_reasoning_process(raw),
            "SELL fill on bearish RSI divergence at the OOS validation bar.",
        )


if __name__ == "__main__":
    unittest.main()
