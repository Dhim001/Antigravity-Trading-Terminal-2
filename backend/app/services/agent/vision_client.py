"""Optional multimodal vision narrator — describe-only, no signals."""

from __future__ import annotations

import base64
import json
import logging

import httpx

from app.config import AGENT_VISION_MODEL, OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_SEC = 20.0

SYSTEM_PROMPT = (
    "You describe chart structure only. Output JSON with keys: structure (string), "
    "patterns (array of strings), notes (string). No trade advice, no BUY/SELL."
)


async def describe_chart(
    symbol: str,
    timeframe: str,
    image_base64: str,
) -> dict | None:
    if not OPENROUTER_API_KEY:
        return None

    data_url = image_base64 if image_base64.startswith("data:") else f"data:image/png;base64,{image_base64}"
    payload = {
        "model": AGENT_VISION_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Describe chart structure for {symbol} on {timeframe} timeframe. "
                            "Return JSON only."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "max_tokens": 400,
        "temperature": 0.2,
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
            model = body.get("model") or AGENT_VISION_MODEL
            parsed = _parse_json_blob(text)
            if not parsed:
                parsed = {"structure": text, "patterns": [], "notes": ""}
            parsed["model"] = model
            return parsed
    except Exception as exc:
        logger.warning("Vision LLM failed: %s", exc)
        return None


def _parse_json_blob(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start: end + 1])
        except json.JSONDecodeError:
            return None
    return None
