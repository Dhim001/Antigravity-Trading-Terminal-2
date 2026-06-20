"""LLM provider protocol — narrators must not alter trading signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

SYSTEM_PROMPT = (
    "You are a concise trading chart analyst. Summarize ONLY the JSON insight provided. "
    "Do not invent prices, indicators, or signals. Do not change the signal field. "
    "When sub_reports are present, mention trend/momentum/risk briefly. "
    "Keep the response under 3 sentences."
)

CRITIQUE_SYSTEM_PROMPT = (
    "You are a trading analyst reviewing structured chart sub-reports. "
    "Respond with valid JSON only, containing exactly these keys: "
    "reasoning_summary (string, 2-3 sentences), risk_notes (string, 1-2 sentences). "
    "Do NOT change, override, or suggest changing the signal field. "
    "Base your answer only on the provided insight JSON."
)

BACKTEST_TRADE_SYSTEM_PROMPT = (
    "You explain ONE specific backtest entry fill. Use ONLY the JSON provided. "
    "Do not invent indicators, signals, or market context not in the JSON. "
    "Do not repeat generic strategy boilerplate across trades. "
    "In 1-2 sentences: why this particular entry (side, price, time, reason) "
    "occurred in the context given. Mention run scope when present (single, sweep best, walk-forward OOS). "
    "If bar_time is present, reference the timing briefly."
)


def extract_assistant_text(message: dict | None) -> str | None:
    """
    Normalize OpenAI-style assistant message text.
    Thinking models (Qwen3, Gemma 4, etc.) may leave content empty and put text in reasoning.
    """
    if not message or not isinstance(message, dict):
        return None

    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    for key in ("reasoning", "thinking", "reasoning_content"):
        val = message.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    return None


@dataclass
class LLMResult:
    text: str | None
    model: str | None
    provider: str | None = None
    latency_ms: float | None = None


class LLMProvider(Protocol):
    name: str

    async def is_available(self) -> bool: ...

    async def chat(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 180,
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> LLMResult: ...

    async def list_models(self) -> list[str]: ...
