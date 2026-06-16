"""Optional LLM narrator — summarizes structured insight JSON only."""

from __future__ import annotations

import logging

import httpx

from app.config import AGENT_LLM_MODEL, OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_SEC = 8.0

SYSTEM_PROMPT = (
    "You are a concise trading chart analyst. Summarize ONLY the JSON insight provided. "
    "Do not invent prices, indicators, or signals. Keep the response under 3 sentences."
)


async def summarize_insight(insight_dict: dict) -> tuple[str | None, str | None]:
    """
    Call OpenRouter (or compatible) chat API.
    Returns (narrative, model) or (None, None) on failure/disabled.
    """
    if not OPENROUTER_API_KEY:
        return None, None

    payload = {
        "model": AGENT_LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Summarize this chart analyst insight for a trader:\n"
                    f"{insight_dict}"
                ),
            },
        ],
        "max_tokens": 180,
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as client:
            resp = await client.post(OPENROUTER_URL, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()
            text = body["choices"][0]["message"]["content"].strip()
            model = body.get("model") or AGENT_LLM_MODEL
            return text, model
    except Exception as exc:
        logger.warning("LLM narrator failed: %s", exc)
        return None, None
