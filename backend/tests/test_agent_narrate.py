"""Agent → Copilot narration: templates, spam suppression, gated LLM polish."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.agent import copilot as copilot_mod
from app.services.agent.copilot import (
    _agent_event_fingerprint,
    _agent_narrate_allowed,
    _llm_polish_keeps_facts,
    _template_agent_narration,
    agent_narrate_event,
)
from app.services.agent.llm.router import LLMResult


class TestAgentNarrate(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        copilot_mod._agent_narrate_seen.clear()

    def test_template_regime_rotation_includes_symbol(self):
        text = _template_agent_narration(
            "RegimeRotation",
            {
                "action": "rotated_strategy",
                "symbol": "BTCUSDT",
                "from_strategy": "MACD_RSI",
                "to_strategy": "SUPERTREND_ADX",
                "regime": "trending",
            },
        )
        self.assertIsNotNone(text)
        self.assertIn("BTCUSDT", text)
        self.assertIn("SUPERTREND_ADX", text)

    def test_template_rejects_status_fluff_actions(self):
        self.assertIsNone(
            _template_agent_narration("RiskSentinel", {"action": "heartbeat"})
        )
        self.assertIsNone(_template_agent_narration("AlphaDecay", {}))

    def test_fingerprint_dedupe_blocks_repeat(self):
        payload = {
            "action": "rotated_strategy",
            "symbol": "BTCUSDT",
            "from_strategy": "MACD_RSI",
            "to_strategy": "SUPERTREND_ADX",
        }
        fp = _agent_event_fingerprint("RegimeRotation", payload)
        self.assertTrue(_agent_narrate_allowed(fp))
        self.assertFalse(_agent_narrate_allowed(fp))

    def test_llm_polish_rejects_missing_facts_and_fluff(self):
        required = ["BTCUSDT", "SUPERTREND_ADX", "trending"]
        self.assertTrue(
            _llm_polish_keeps_facts(
                "Rotated BTCUSDT to SUPERTREND_ADX for trending regime.",
                required,
            )
        )
        self.assertFalse(
            _llm_polish_keeps_facts("I am online and ready to help.", required)
        )
        self.assertFalse(
            _llm_polish_keeps_facts("Rotated to SUPERTREND_ADX.", required)
        )

    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    @patch("app.services.agent.copilot.copilot_store")
    async def test_agent_narrate_skips_duplicate_broadcast(self, mock_store):
        mock_store.ensure_session_id.return_value = "default"
        mock_store.append_message.return_value = {
            "id": "m1",
            "payload": {},
        }
        payload = {
            "action": "decay_detected",
            "symbol": "ETHUSDT",
            "bot_id": "b1",
            "reasons": ["win-rate dropped"],
            "auto_paused": True,
        }
        with patch.object(
            copilot_mod, "_broadcast_copilot_agent_message", new_callable=AsyncMock
        ) as mock_bcast:
            with patch(
                "app.services.agent.llm.router.get_llm_status", new_callable=AsyncMock
            ) as st:
                st.return_value = {"available": False}
                await agent_narrate_event("AlphaDecay", payload)
                await agent_narrate_event("AlphaDecay", payload)
            self.assertEqual(mock_bcast.await_count, 1)
            self.assertEqual(mock_store.append_message.call_count, 1)
            stored_payload = mock_store.append_message.call_args.kwargs["payload"]
            self.assertEqual(stored_payload["narration_source"], "template")

    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", True)
    @patch("app.services.agent.copilot.copilot_store")
    async def test_llm_polish_when_provider_online(self, mock_store):
        mock_store.ensure_session_id.return_value = "default"
        mock_store.append_message.return_value = {"id": "m2", "payload": {}}
        payload = {
            "action": "rotated_strategy",
            "symbol": "BTCUSDT",
            "bot_id": "b2",
            "from_strategy": "MACD_RSI",
            "to_strategy": "SUPERTREND_ADX",
            "regime": "trending",
            "why": "ADX rising with directional bias.",
        }
        polished = (
            "Rotated BTCUSDT from MACD_RSI to SUPERTREND_ADX after a trending regime shift."
        )
        with patch.object(
            copilot_mod, "_broadcast_copilot_agent_message", new_callable=AsyncMock
        ) as mock_bcast:
            with patch(
                "app.services.agent.llm.router.get_llm_status", new_callable=AsyncMock
            ) as st:
                with patch(
                    "app.services.agent.copilot._chat", new_callable=AsyncMock
                ) as chat:
                    st.return_value = {"available": True, "provider": "ollama"}
                    chat.return_value = LLMResult(
                        text=polished, model="local", provider="ollama"
                    )
                    await agent_narrate_event("RegimeRotation", payload)

        self.assertEqual(mock_bcast.await_count, 1)
        kwargs = mock_store.append_message.call_args.kwargs
        self.assertEqual(kwargs["content"], polished)
        self.assertEqual(kwargs["payload"]["narration_source"], "llm")
        self.assertEqual(kwargs["payload"]["provider"], "ollama")
        self.assertEqual(mock_bcast.await_args.args[0]["narration_source"], "llm")

    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.copilot_store")
    async def test_llm_skipped_when_use_llm_disabled(self, mock_store):
        mock_store.ensure_session_id.return_value = "default"
        mock_store.append_message.return_value = {"id": "m3", "payload": {}}
        payload = {
            "action": "paused_single_bot",
            "symbol": "ETHUSDT",
            "bot_id": "b3",
            "reason": "Hit max consecutive losses (3).",
        }
        with patch.object(
            copilot_mod, "_broadcast_copilot_agent_message", new_callable=AsyncMock
        ):
            with patch(
                "app.services.agent.llm.router.get_llm_status", new_callable=AsyncMock
            ) as st:
                with patch(
                    "app.services.agent.copilot._chat", new_callable=AsyncMock
                ) as chat:
                    st.return_value = {"available": True}
                    await agent_narrate_event("RiskSentinel", payload)
                    chat.assert_not_awaited()

        kwargs = mock_store.append_message.call_args.kwargs
        self.assertEqual(kwargs["payload"]["narration_source"], "template")
        self.assertIn("ETHUSDT", kwargs["content"])

    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", True)
    @patch("app.services.agent.copilot.copilot_store")
    async def test_llm_rejected_when_facts_dropped(self, mock_store):
        mock_store.ensure_session_id.return_value = "default"
        mock_store.append_message.return_value = {"id": "m4", "payload": {}}
        payload = {
            "action": "rotated_strategy",
            "symbol": "BTCUSDT",
            "from_strategy": "MACD_RSI",
            "to_strategy": "SUPERTREND_ADX",
            "regime": "trending",
        }
        with patch.object(
            copilot_mod, "_broadcast_copilot_agent_message", new_callable=AsyncMock
        ):
            with patch(
                "app.services.agent.llm.router.get_llm_status", new_callable=AsyncMock
            ) as st:
                with patch(
                    "app.services.agent.copilot._chat", new_callable=AsyncMock
                ) as chat:
                    st.return_value = {"available": True}
                    chat.return_value = LLMResult(
                        text="I am online and monitoring.",
                        model="x",
                        provider="ollama",
                    )
                    await agent_narrate_event("RegimeRotation", payload)

        kwargs = mock_store.append_message.call_args.kwargs
        self.assertEqual(kwargs["payload"]["narration_source"], "template")
        self.assertIn("BTCUSDT", kwargs["content"])


if __name__ == "__main__":
    unittest.main()
