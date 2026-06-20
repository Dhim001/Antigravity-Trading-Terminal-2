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
)


class TestLLMRouter(unittest.TestCase):
    def test_resolve_model_explicit(self):
        self.assertEqual(resolve_model("custom-model"), "custom-model")

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

    def test_summarize_insight_returns_none_when_off(self):
        async def _run():
            with patch("app.services.agent.llm.router.LLM_PROVIDER", "off"):
                text, model, provider = await summarize_insight({"signal": "BUY"})
                self.assertIsNone(text)
                self.assertIsNone(model)
                self.assertEqual(provider, "off")

        import asyncio
        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
