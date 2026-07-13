"""Local Ollama LLM provider (OpenAI-compatible chat API)."""

from __future__ import annotations

import logging
import time

import httpx

from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_REASONING_EFFORT, OLLAMA_TIMEOUT_SEC
from app.services.agent.llm.base import LLMResult, extract_assistant_text

logger = logging.getLogger(__name__)

TIMEOUT_SEC = OLLAMA_TIMEOUT_SEC
_TAGS_CACHE_SEC = 30.0

_tags_cache: tuple[float, list[str]] | None = None
_reachable_cache: tuple[float, bool] | None = None


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or OLLAMA_BASE_URL).rstrip("/")

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}/v1/chat/completions"

    @property
    def tags_url(self) -> str:
        return f"{self.base_url}/api/tags"

    async def is_available(self) -> bool:
        global _reachable_cache
        now = time.monotonic()
        if _reachable_cache and (now - _reachable_cache[0]) < _TAGS_CACHE_SEC:
            return _reachable_cache[1]
        ok = False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(self.tags_url)
                ok = resp.status_code == 200
        except Exception:
            ok = False
        _reachable_cache = (now, ok)
        return ok

    async def list_models(self) -> list[str]:
        global _tags_cache
        now = time.monotonic()
        if _tags_cache and (now - _tags_cache[0]) < _TAGS_CACHE_SEC:
            return _tags_cache[1]

        models: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(self.tags_url)
                resp.raise_for_status()
                body = resp.json()
                for item in body.get("models") or []:
                    name = item.get("name")
                    if name:
                        models.append(str(name))
        except Exception as exc:
            logger.debug("Ollama list_models failed: %s", exc)

        if not models:
            models = [OLLAMA_MODEL] if OLLAMA_MODEL else []

        _tags_cache = (now, models)
        return models

    async def chat(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 180,
        temperature: float = 0.3,
        json_mode: bool = False,
        messages: list[dict] | None = None,
        tools: list[dict] | None = None,
        timeout: float | None = None,
    ) -> LLMResult:
        chosen = model or OLLAMA_MODEL
        msgs = messages if messages is not None else [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        payload: dict = {
            "model": chosen,
            "messages": msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            payload["format"] = "json"
        if tools:
            payload["tools"] = tools
        if OLLAMA_REASONING_EFFORT:
            payload["reasoning_effort"] = OLLAMA_REASONING_EFFORT

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout or TIMEOUT_SEC) as client:
                resp = await client.post(self.chat_url, json=payload)
                resp.raise_for_status()
                body = resp.json()
                choices = body.get("choices") or []
                if not choices:
                    logger.warning("Ollama returned no choices model=%s body=%s", chosen, body.get("error"))
                    return LLMResult(text=None, model=chosen, provider=self.name)

                choice = choices[0]
                message = choice.get("message") or {}
                text = extract_assistant_text(message)
                tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
                if not text and not tool_calls:
                    logger.warning(
                        "Ollama empty assistant text model=%s finish_reason=%s message_keys=%s",
                        chosen,
                        choice.get("finish_reason"),
                        list(message.keys()),
                    )
                resolved = body.get("model") or chosen
                return LLMResult(
                    text=text,
                    model=resolved,
                    provider=self.name,
                    latency_ms=(time.monotonic() - t0) * 1000,
                    message=message if isinstance(message, dict) else None,
                    tool_calls=tool_calls if isinstance(tool_calls, list) else None,
                )
        except Exception as exc:
            logger.warning("Ollama LLM failed: %s", exc)
            return LLMResult(text=None, model=None, provider=self.name)
