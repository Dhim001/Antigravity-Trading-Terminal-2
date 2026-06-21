"""LLM router — provider selection and availability."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.agent.llm.router import (
    _pick_provider,
    get_preferred_model,
    resolve_model,
    set_preferred_model,
    summarize_insight,
    summarize_trade_explain,
)
from app.services.agent.llm.payloads import _slim_sub_reports, slim_trade_explain_payload


class TestLLMRouter(unittest.TestCase):
    def test_resolve_model_explicit(self):
        self.assertEqual(resolve_model("custom-model"), "custom-model")

    def test_resolve_model_narrator_tier(self):
        with patch("app.services.agent.llm.router._preferred_model", None):
            with patch("app.services.agent.llm.router.OLLAMA_MODEL_NARRATOR", "llama3.2:3b"):
                with patch("app.services.agent.llm.router.LLM_PROVIDER", "ollama"):
                    with patch("app.services.agent.llm.router.TERMINAL_MODE", "SIMULATED"):
                        self.assertEqual(resolve_model(task="narrator"), "llama3.2:3b")

    def test_resolve_model_deep_tier(self):
        with patch("app.services.agent.llm.router._preferred_model", None):
            with patch("app.services.agent.llm.router.OLLAMA_MODEL_DEEP", "qwen2.5:7b"):
                with patch("app.services.agent.llm.router.LLM_PROVIDER", "ollama"):
                    with patch("app.services.agent.llm.router.TERMINAL_MODE", "SIMULATED"):
                        self.assertEqual(resolve_model(task="deep"), "qwen2.5:7b")

    def test_preferred_model_override(self):
        set_preferred_model("phi3:mini")
        self.assertEqual(get_preferred_model(), "phi3:mini")
        self.assertEqual(resolve_model(), "phi3:mini")
        set_preferred_model(None)

    def test_pick_provider_off(self):
        async def _run():
            with patch("app.services.agent.llm.router.LLM_PROVIDER", "off"):
                provider, name = await _pick_provider()
                self.assertIsNone(provider)
                self.assertEqual(name, "off")

        import asyncio
        asyncio.run(_run())

    def test_pick_provider_auto_prefers_ollama_in_sim(self):
        async def _run():
            mock_ollama = AsyncMock()
            mock_ollama.is_available.return_value = True
            mock_openrouter = AsyncMock()
            mock_openrouter.is_available.return_value = True
            with patch("app.services.agent.llm.router.LLM_PROVIDER", "auto"):
                with patch("app.services.agent.llm.router.TERMINAL_MODE", "SIMULATED"):
                    with patch("app.services.agent.llm.router._ollama", mock_ollama):
                        with patch("app.services.agent.llm.router._openrouter", mock_openrouter):
                            provider, name = await _pick_provider()
                            self.assertEqual(name, "ollama")
                            self.assertIs(provider, mock_ollama)

        import asyncio
        asyncio.run(_run())

    def test_summarize_insight_returns_template_when_off(self):
        async def _run():
            with patch("app.services.agent.llm.router.LLM_PROVIDER", "off"):
                text, model, provider = await summarize_insight({
                    "symbol": "AAPL",
                    "signal": "BUY",
                    "confidence": 0.7,
                    "reasons": ["RSI cross"],
                })
                self.assertIsNotNone(text)
                self.assertIn("BUY", text)
                self.assertIsNone(model)
                self.assertEqual(provider, "off")

        import asyncio
        asyncio.run(_run())

    def test_slim_sub_reports_includes_reasons_and_risk(self):
        slim = _slim_sub_reports({
            "trend": {"score": 2, "reasons": ["uptrend"]},
            "indicator": {"score": 1, "reasons": ["RSI 42"]},
            "risk": {"atr_regime": "elevated", "suggested_size_factor": 0.8, "reasons": ["high vol"]},
        })
        self.assertEqual(slim["trend"]["reasons"], ["uptrend"])
        self.assertEqual(slim["risk"]["atr_regime"], "elevated")

    def test_summarize_trade_explain_returns_template_when_off(self):
        async def _run():
            with patch("app.services.agent.llm.router.LLM_PROVIDER", "off"):
                bundle = slim_trade_explain_payload({
                    "insight": {
                        "symbol": "BTCUSDT",
                        "signal": "BUY",
                        "confidence": 0.7,
                        "reasons": ["MACD cross"],
                        "sub_reports": {"risk": {"atr_regime": "normal"}},
                    },
                    "trade_context": {"side": "BUY", "price": 100},
                    "recent_logs": ["Chart signal BUY fired"],
                })
                text, model, provider = await summarize_trade_explain(bundle)
                self.assertIsNotNone(text)
                self.assertIn("BUY", text)
                self.assertEqual(provider, "off")

        import asyncio
        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
