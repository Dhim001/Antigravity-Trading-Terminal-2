"""Validate LLM narratives before accepting; optional single strict retry."""

from __future__ import annotations

import re
from typing import Any

from app.services.agent.llm.base import strip_reasoning_process

_MIN_LEN = 12
_MAX_LEN = 520

_STRICT_RETRY_USER_SUFFIX = (
    "\n\nIMPORTANT: Respond with ONLY valid JSON "
    '{"explanation": "..."}. The explanation MUST explicitly mention the '
    "trade side (BUY or SELL) from DATA. No chain-of-thought."
)

_SIDE_ALIASES = {
    "BUY": ("buy", "long"),
    "SELL": ("sell", "short"),
}


def _expected_side(context: dict[str, Any]) -> str | None:
    side = context.get("signal") or context.get("side")
    if side in ("BUY", "SELL"):
        return side
    tc = context.get("trade_context")
    if isinstance(tc, dict) and tc.get("side") in ("BUY", "SELL"):
        return tc["side"]
    if context.get("analyst_signal") in ("BUY", "SELL"):
        return context["analyst_signal"]
    insight = context.get("insight")
    if isinstance(insight, dict) and insight.get("signal") in ("BUY", "SELL"):
        return insight["signal"]
    return None


def _mentions_side(text: str, side: str) -> bool:
    lower = text.lower()
    for token in _SIDE_ALIASES.get(side, (side.lower(),)):
        if re.search(rf"\b{re.escape(token)}\b", lower):
            return True
    return side.lower() in lower


def validate_narrative(
    narrative: str | None,
    *,
    context: dict[str, Any] | None = None,
    require_side: bool = True,
) -> tuple[bool, str | None]:
    """
    Return (ok, reason_code).
    reason_code: too_short | too_long | missing_side | cot_junk | empty
    """
    if not narrative or not narrative.strip():
        return False, "empty"

    text = narrative.strip()
    if len(text) < _MIN_LEN:
        return False, "too_short"
    if len(text) > _MAX_LEN:
        return False, "too_long"

    cleaned = strip_reasoning_process(text)
    if not cleaned:
        return False, "cot_junk"
    if cleaned != text and len(cleaned) < _MIN_LEN:
        return False, "cot_junk"

    if require_side and context:
        side = _expected_side(context)
        if side and not _mentions_side(cleaned, side):
            return False, "missing_side"

    return True, None


def strict_retry_user_suffix() -> str:
    return _STRICT_RETRY_USER_SUFFIX
