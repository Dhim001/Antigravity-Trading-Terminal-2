"""Domain-specific prompt library for the Trade Copilot (AI-FT-PTL-001 §3.5, P2 #13).

Versioned system prompts + few-shot exemplars per intent type (analysis,
explain, action). Best-matching exemplars are retrieved via token-overlap
similarity on the user query (no embedding model required — the same keyword
fallback pattern used by ``vision_store``). User corrections can be logged as
negative examples for prompt refinement.

Library files live in ``prompt_library/`` next to this module as versioned
JSON: ``{intent}.json`` with ``{"version", "system", "exemplars": [...]}``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_LIBRARY_DIR = os.path.join(os.path.dirname(__file__), "prompt_library")
_NEGATIVE_LOG = os.path.join(_LIBRARY_DIR, "negative_examples.jsonl")

_INTENTS = ("analysis", "explain", "action")

_cache: dict[str, dict[str, Any]] = {}


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def _load(intent: str) -> dict[str, Any] | None:
    intent = str(intent or "").lower()
    if intent in _cache:
        return _cache[intent]
    path = os.path.join(_LIBRARY_DIR, f"{intent}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("system"):
            _cache[intent] = data
            return data
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.debug("prompt library load failed for %s: %s", intent, exc)
    return None


def library_version(intent: str) -> str | None:
    data = _load(intent)
    return str(data.get("version")) if data else None


def retrieve_exemplars(
    intent: str,
    query: str,
    *,
    max_exemplars: int | None = None,
) -> list[dict[str, str]]:
    """Return the best-matching few-shot exemplars for ``query``.

    Scored by Jaccard token overlap between the query and each exemplar's
    ``user`` turn; ties broken by file order. Returns [] when the intent has
    no library entry.
    """
    from app.config import COPILOT_PROMPT_EXEMPLARS_MAX

    data = _load(intent)
    if not data:
        return []
    exemplars = data.get("exemplars") or []
    if not exemplars:
        return []
    k = max(1, int(max_exemplars or COPILOT_PROMPT_EXEMPLARS_MAX))

    q_tokens = _tokenize(query)
    if not q_tokens:
        return list(exemplars[:k])

    scored: list[tuple[float, int, dict]] = []
    for idx, ex in enumerate(exemplars):
        ex_tokens = _tokenize((ex or {}).get("user"))
        if not ex_tokens:
            continue
        overlap = len(q_tokens & ex_tokens)
        union = len(q_tokens | ex_tokens) or 1
        scored.append((overlap / union, idx, ex))
    scored.sort(key=lambda t: (-t[0], t[1]))
    # Drop zero-overlap matches — a generic exemplar is better than noise.
    best = [ex for score, _idx, ex in scored if score > 0.0]
    if not best:
        best = list(exemplars[:k])
    return best[:k]


def build_system_prompt(intent: str, query: str) -> str | None:
    """Compose the versioned system prompt + retrieved few-shot exemplars.

    Returns ``None`` when the intent has no library entry (caller keeps its
    default system prompt).
    """
    data = _load(intent)
    if not data:
        return None
    system = str(data.get("system") or "").strip()
    if not system:
        return None

    exemplars = retrieve_exemplars(intent, query)
    if not exemplars:
        return system

    shots: list[str] = []
    for ex in exemplars:
        user = str(ex.get("user") or "").strip()
        assistant = str(ex.get("assistant") or "").strip()
        if user and assistant:
            shots.append(f"User: {user}\nAssistant: {assistant}")
    if not shots:
        return system
    return system + "\n\nFew-shot examples:\n\n" + "\n\n".join(shots)


def log_negative_example(intent: str, query: str, correction: str) -> None:
    """Log a user correction as a negative example for prompt refinement."""
    try:
        os.makedirs(_LIBRARY_DIR, exist_ok=True)
        rec = {
            "intent": str(intent or "").lower(),
            "query": str(query or "")[:500],
            "correction": str(correction or "")[:500],
        }
        with open(_NEGATIVE_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        logger.debug("negative example log skipped", exc_info=True)


def available_intents() -> list[str]:
    return [i for i in _INTENTS if _load(i) is not None]
