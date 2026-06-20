"""OpenRouter cloud LLM provider."""

from __future__ import annotations

import logging
import time

import httpx

from app.config import AGENT_LLM_MODEL, OPENROUTER_API_KEY
from app.services.agent.llm.base import LLMResult, extract_assistant_text

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_SEC = 8.0


class OpenRouterProvider:
    name = "openrouter"

    async def is_available(self) -> bool:
        return bool(OPENROUTER_API_KEY)

    async def list_models(self) -> list[str]:
        if not OPENROUTER_API_KEY:
            return []
        return [AGENT_LLM_MODEL]

    async def chat(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 180,
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> LLMResult:
        if not OPENROUTER_API_KEY:
            return LLMResult(text=None, model=None, provider=self.name)

        chosen = model or AGENT_LLM_MODEL
        payload: dict = {
            "model": chosen,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as client:
                resp = await client.post(OPENROUTER_URL, json=payload, headers=headers)
                resp.raise_for_status()
                body = resp.json()
                choices = body.get("choices") or []
                if not choices:
                    return LLMResult(text=None, model=chosen, provider=self.name)
                message = choices[0].get("message") or {}
                text = extract_assistant_text(message)
                resolved = body.get("model") or chosen
                return LLMResult(
                    text=text,
                    model=resolved,
                    provider=self.name,
                    latency_ms=(time.monotonic() - t0) * 1000,
                )
        except Exception as exc:
            logger.warning("OpenRouter LLM failed: %s", exc)
            return LLMResult(text=None, model=None, provider=self.name)
