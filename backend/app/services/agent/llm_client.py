"""Optional LLM narrator — summarizes structured insight JSON only."""

from __future__ import annotations

from app.services.agent.llm.router import summarize_insight, summarize_with_critique

__all__ = ["summarize_insight", "summarize_with_critique"]
