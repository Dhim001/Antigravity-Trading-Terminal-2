"""Route LLM calls to Ollama, OpenRouter, or off based on config."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from app.config import (
    AGENT_LLM_FALLBACK_CLOUD,
    AGENT_LLM_MODEL,
    AGENT_LLM_MODEL_DEEP,
    AGENT_LLM_PREFER_LOCAL,
    LLM_PROVIDER,
    OLLAMA_MODEL,
    OLLAMA_MODEL_DEEP,
    OLLAMA_MODEL_NARRATOR,
    TERMINAL_MODE,
)
from app.services.agent.llm.base import (
    BACKTEST_JSON_SYSTEM_PROMPT,
    CRITIQUE_SYSTEM_PROMPT,
    LLMResult,
    NARRATOR_JSON_SYSTEM_PROMPT,
    TRADE_EXPLAIN_JSON_SYSTEM_PROMPT,
    finalize_narrative,
    parse_json_object,
    strip_reasoning_process,
)
from app.services.agent.llm.model_registry import enrich_models_response, lookup_model_meta
from app.services.agent.llm.ollama import OllamaProvider
from app.services.agent.llm.openrouter import OpenRouterProvider
from app.services.agent.llm.payloads import (
    dumps_payload,
    slim_backtest_trade_payload,
    slim_insight_payload,
    slim_trade_explain_payload,
    template_backtest_narrative,
    template_insight_narrative,
    template_trade_explain_narrative,
)
from app.services.agent.llm.validation import strict_retry_user_suffix, validate_narrative

logger = logging.getLogger(__name__)

_ollama = OllamaProvider()
_openrouter = OpenRouterProvider()
_preferred_model: str | None = None

LlmTask = Literal["narrator", "deep"]


def set_preferred_model(model: str | None) -> None:
    global _preferred_model
    _preferred_model = (model or "").strip() or None


def get_preferred_model() -> str | None:
    return _preferred_model


def _ollama_task_default(task: LlmTask) -> str:
    if task == "deep":
        return OLLAMA_MODEL_DEEP or OLLAMA_MODEL
    return OLLAMA_MODEL_NARRATOR or OLLAMA_MODEL


def resolve_model(
    explicit: str | None = None,
    task: LlmTask = "narrator",
    *,
    provider: str | None = None,
) -> str:
    """Pick model: UI override > preferred > provider tier > default."""
    if explicit:
        return explicit
    if _preferred_model:
        return _preferred_model

    pname = (provider or "").lower()
    use_ollama = pname == "ollama" or (
        not pname and LLM_PROVIDER in ("ollama", "auto")
    )

    if task == "deep":
        if use_ollama:
            return _ollama_task_default("deep")
        return AGENT_LLM_MODEL_DEEP or AGENT_LLM_MODEL
    if use_ollama:
        return _ollama_task_default("narrator")
    return AGENT_LLM_MODEL


async def _coerce_ollama_model(wanted: str, *, task: LlmTask = "narrator") -> str:
    """Map cloud/preferred ids to an installed Ollama tag when possible."""
    models = await _ollama.list_models()
    if not models:
        return wanted or _ollama_task_default(task)

    if wanted and wanted in models:
        return wanted

    if wanted:
        base = wanted.split(":")[0].lower()
        for name in models:
            if name.lower() == wanted.lower() or name.lower().startswith(f"{base}:"):
                return name

    default = _ollama_task_default(task)
    if default in models:
        return default
    default_base = default.split(":")[0].lower()
    for name in models:
        if name.lower().startswith(f"{default_base}:"):
            return name

    for name in models:
        meta = lookup_model_meta(name)
        if meta.get("tier") == ("deep" if task == "deep" else "narrator") and meta.get("recommended"):
            return name

    return models[0]


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
    active_model = resolve_model(provider=name if provider else "off")
    if name == "ollama":
        active_model = await _coerce_ollama_model(active_model, task="narrator")
    return enrich_models_response({
        "provider": name if provider else "off",
        "configured_provider": LLM_PROVIDER,
        "ollama_reachable": ollama_up,
        "openrouter_configured": openrouter_up,
        "available": provider is not None,
        "model": active_model,
        "preferred_model": _preferred_model,
        "narrator_model": resolve_model(task="narrator"),
        "deep_model": resolve_model(task="deep"),
        "models": models,
        "openrouter_model": AGENT_LLM_MODEL if openrouter_up else None,
    })


async def list_all_models() -> dict[str, Any]:
    ollama_models = await _ollama.list_models() if await _ollama.is_available() else []
    openrouter_models = await _openrouter.list_models() if await _openrouter.is_available() else []
    _, provider_name = await _pick_provider()
    if provider_name == "off":
        if ollama_models:
            provider_name = "ollama"
        elif openrouter_models:
            provider_name = "openrouter"
    return enrich_models_response({
        "ollama": ollama_models,
        "openrouter": openrouter_models,
        "active_model": resolve_model(provider=provider_name),
        "preferred_model": _preferred_model,
        "narrator_model": resolve_model(task="narrator", provider=provider_name),
        "deep_model": resolve_model(task="deep", provider=provider_name),
    })


async def _chat(
    *,
    system: str,
    user: str,
    model: str | None = None,
    task: LlmTask = "narrator",
    max_tokens: int = 180,
    temperature: float = 0.3,
    json_mode: bool = False,
) -> LLMResult:
    provider, provider_name = await _pick_provider()
    if provider is None:
        return LLMResult(text=None, model=None, provider="off")

    chosen = resolve_model(model, task=task, provider=provider_name)
    if provider.name == "ollama":
        chosen = await _coerce_ollama_model(chosen, task=task)
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
        fb_model = AGENT_LLM_MODEL_DEEP if task == "deep" else AGENT_LLM_MODEL
        fb = await _openrouter.chat(
            system=system,
            user=user,
            model=fb_model,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
        )
        if fb.text:
            fb.provider = "openrouter"
        return fb

    return result


async def _narrate_json_validated(
    *,
    system: str,
    user: str,
    context: dict,
    fallback: str | None,
    model: str | None,
    task: LlmTask,
    max_tokens: int,
    temperature: float,
    require_side: bool = True,
) -> tuple[str | None, str | None, str | None]:
    """JSON-mode narration with validation and one strict retry."""
    result = await _chat(
        system=system,
        user=user,
        model=model,
        task=task,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=True,
    )
    narrative = finalize_narrative(result.text, None)
    ok, reason = validate_narrative(narrative, context=context, require_side=require_side)
    if not ok and result.text:
        logger.debug("LLM narrative rejected (%s), retrying once", reason)
        retry = await _chat(
            system=system,
            user=user + strict_retry_user_suffix(),
            model=model,
            task=task,
            max_tokens=max_tokens,
            temperature=0.15,
            json_mode=True,
        )
        retry_narrative = finalize_narrative(retry.text, None)
        ok2, _ = validate_narrative(retry_narrative, context=context, require_side=require_side)
        if ok2 and retry_narrative:
            return retry_narrative, retry.model or result.model, retry.provider or result.provider
        if retry.text and not narrative:
            narrative = finalize_narrative(retry.text, fallback)

    if narrative and ok:
        return narrative, result.model, result.provider
    if narrative and not ok:
        logger.debug("LLM narrative failed validation (%s), using template fallback", reason)
    return fallback, result.model, result.provider


async def summarize_insight(
    insight_dict: dict,
    *,
    model: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Returns (narrative, model, provider). JSON-mode with template fallback."""
    slim = slim_insight_payload(insight_dict)
    fallback = template_insight_narrative(slim) or template_insight_narrative(insight_dict)
    provider, provider_name = await _pick_provider()
    if provider is None:
        return fallback, None, provider_name

    user = f"DATA:\n{dumps_payload(slim)}"
    return await _narrate_json_validated(
        system=NARRATOR_JSON_SYSTEM_PROMPT,
        user=user,
        context=slim,
        fallback=fallback,
        model=model,
        task="narrator",
        max_tokens=120,
        temperature=0.25,
        require_side=True,
    )


async def summarize_trade_explain(
    bundle: dict,
    *,
    model: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Post-trade explain — dedicated prompt with RAG context."""
    slim = slim_trade_explain_payload(bundle)
    fallback = template_trade_explain_narrative(slim) or template_trade_explain_narrative(bundle)
    provider, provider_name = await _pick_provider()
    if provider is None:
        return fallback, None, provider_name

    user = f"DATA:\n{dumps_payload(slim)}"
    return await _narrate_json_validated(
        system=TRADE_EXPLAIN_JSON_SYSTEM_PROMPT,
        user=user,
        context=slim,
        fallback=fallback,
        model=model,
        task="narrator",
        max_tokens=180,
        temperature=0.3,
        require_side=False,
    )


async def summarize_backtest_entry(
    bundle: dict,
    *,
    model: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Narrate one backtest entry — JSON-mode with template fallback."""
    slim = slim_backtest_trade_payload(bundle)
    fallback = template_backtest_narrative(slim) or template_backtest_narrative(bundle)
    provider, provider_name = await _pick_provider()
    if provider is None:
        return fallback, None, provider_name

    user = f"DATA:\n{dumps_payload(slim)}"
    return await _narrate_json_validated(
        system=BACKTEST_JSON_SYSTEM_PROMPT,
        user=user,
        context=slim,
        fallback=fallback,
        model=model,
        task="narrator",
        max_tokens=140,
        temperature=0.35,
        require_side=True,
    )


async def summarize_with_critique(
    insight_dict: dict,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    """Structured enrichment — metadata only, never alters signal."""
    slim = slim_insight_payload(insight_dict)
    result = await _chat(
        system=CRITIQUE_SYSTEM_PROMPT,
        user=f"DATA:\n{dumps_payload(slim)}",
        model=model,
        task="deep",
        max_tokens=280,
        temperature=0.25,
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
        summary_fb = template_insight_narrative(slim)
        if summary_fb:
            out["reasoning_summary"] = summary_fb
        return out
    parsed = parse_json_object(result.text)
    if parsed:
        out["reasoning_summary"] = finalize_narrative(
            parsed.get("reasoning_summary"),
            template_insight_narrative(slim),
        )
        risk = parsed.get("risk_notes")
        out["risk_notes"] = strip_reasoning_process(risk) if isinstance(risk, str) else None
    else:
        out["reasoning_summary"] = finalize_narrative(
            result.text[:500],
            template_insight_narrative(slim),
        )
    return out
