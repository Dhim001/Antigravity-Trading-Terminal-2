"""Route LLM calls to Ollama, OpenRouter, or off based on config."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import (
    AGENT_LLM_FALLBACK_CLOUD,
    AGENT_LLM_MODEL,
    AGENT_LLM_PREFER_LOCAL,
    LLM_PROVIDER,
    OLLAMA_MODEL,
    TERMINAL_MODE,
)
from app.services.agent.llm.base import (
    CRITIQUE_SYSTEM_PROMPT,
    LLMResult,
    SYSTEM_PROMPT,
)
from app.services.agent.llm.ollama import OllamaProvider
from app.services.agent.llm.openrouter import OpenRouterProvider

logger = logging.getLogger(__name__)

_ollama = OllamaProvider()
_openrouter = OpenRouterProvider()
_preferred_model: str | None = None


def set_preferred_model(model: str | None) -> None:
    global _preferred_model
    _preferred_model = (model or "").strip() or None


def get_preferred_model() -> str | None:
    return _preferred_model


def resolve_model(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if _preferred_model:
        return _preferred_model
    if LLM_PROVIDER in ("ollama", "auto") and TERMINAL_MODE == "SIMULATED":
        return OLLAMA_MODEL
    return AGENT_LLM_MODEL


async def _pick_provider() -> tuple[Any, str] | tuple[None, str]:
    """Return (provider, resolved_name) or (None, 'off')."""
    mode = LLM_PROVIDER.lower()

    if mode == "off":
        return None, "off"

    if mode == "openrouter":
        if await _openrouter.is_available():
            return _openrouter, "openrouter"
        return None, "off"

    if mode == "ollama":
        if await _ollama.is_available():
            return _ollama, "ollama"
        return None, "off"

    # auto
    prefer_local = AGENT_LLM_PREFER_LOCAL or TERMINAL_MODE == "SIMULATED"
    if prefer_local and await _ollama.is_available():
        return _ollama, "ollama"
    if await _openrouter.is_available():
        return _openrouter, "openrouter"
    if AGENT_LLM_FALLBACK_CLOUD and await _openrouter.is_available():
        return _openrouter, "openrouter"
    if await _ollama.is_available():
        return _ollama, "ollama"
    return None, "off"


async def is_llm_available() -> bool:
    provider, _ = await _pick_provider()
    return provider is not None


async def get_llm_status() -> dict[str, Any]:
    provider, name = await _pick_provider()
    ollama_up = await _ollama.is_available()
    openrouter_up = await _openrouter.is_available()
    models: list[str] = []
    if ollama_up:
        try:
            models = await _ollama.list_models()
        except Exception:
            models = []
    active_model = resolve_model()
    return {
        "provider": name if provider else "off",
        "configured_provider": LLM_PROVIDER,
        "ollama_reachable": ollama_up,
        "openrouter_configured": openrouter_up,
        "available": provider is not None,
        "model": active_model,
        "preferred_model": _preferred_model,
        "models": models,
        "openrouter_model": AGENT_LLM_MODEL if openrouter_up else None,
    }


async def list_all_models() -> dict[str, Any]:
    ollama_models = await _ollama.list_models() if await _ollama.is_available() else []
    openrouter_models = await _openrouter.list_models() if await _openrouter.is_available() else []
    return {
        "ollama": ollama_models,
        "openrouter": openrouter_models,
        "active_model": resolve_model(),
        "preferred_model": _preferred_model,
    }


async def _chat(
    *,
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int = 180,
    temperature: float = 0.3,
    json_mode: bool = False,
) -> LLMResult:
    provider, _ = await _pick_provider()
    if provider is None:
        return LLMResult(text=None, model=None, provider="off")

    chosen = resolve_model(model)
    result = await provider.chat(
        system=system,
        user=user,
        model=chosen,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=json_mode,
    )
    if result.text:
        return result

    # Fallback: auto mode only, if primary was Ollama and cloud allowed
    if (
        LLM_PROVIDER == "auto"
        and provider.name == "ollama"
        and AGENT_LLM_FALLBACK_CLOUD
        and await _openrouter.is_available()
    ):
        fb = await _openrouter.chat(
            system=system,
            user=user,
            model=AGENT_LLM_MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
        )
        if fb.text:
            fb.provider = "openrouter"
        return fb

    return result


async def summarize_insight(
    insight_dict: dict,
    *,
    model: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Returns (narrative, model, provider)."""
    result = await _chat(
        system=SYSTEM_PROMPT,
        user=f"Summarize this chart analyst insight for a trader:\n{insight_dict}",
        model=model,
    )
    return result.text, result.model, result.provider


async def summarize_with_critique(
    insight_dict: dict,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    """Structured enrichment — metadata only, never alters signal."""
    result = await _chat(
        system=CRITIQUE_SYSTEM_PROMPT,
        user=f"Review this chart analyst insight:\n{json.dumps(insight_dict)}",
        model=model,
        max_tokens=280,
        json_mode=True,
    )
    out: dict[str, Any] = {
        "reasoning_summary": None,
        "risk_notes": None,
        "model": result.model,
        "provider": result.provider,
        "latency_ms": result.latency_ms,
    }
    if not result.text:
        return out
    try:
        parsed = json.loads(result.text)
        out["reasoning_summary"] = parsed.get("reasoning_summary")
        out["risk_notes"] = parsed.get("risk_notes")
    except json.JSONDecodeError:
        out["reasoning_summary"] = result.text[:500]
    return out
