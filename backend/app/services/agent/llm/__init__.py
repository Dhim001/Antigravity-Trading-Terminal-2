"""LLM providers — Ollama (local) and OpenRouter (cloud)."""

from app.services.agent.llm.router import (
    get_llm_status,
    is_llm_available,
    list_all_models,
    set_preferred_model,
    summarize_backtest_entry,
    summarize_insight,
    summarize_trade_explain,
    summarize_with_critique,
)

__all__ = [
    "get_llm_status",
    "is_llm_available",
    "list_all_models",
    "set_preferred_model",
    "summarize_backtest_entry",
    "summarize_insight",
    "summarize_trade_explain",
    "summarize_with_critique",
]
