"""Curated model metadata for LLM tier routing and Settings UI."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_REGISTRY_PATH = Path(__file__).with_name("models.json")

_DEFAULT: dict[str, Any] = {"models": [], "patterns": []}


@lru_cache(maxsize=1)
def _load_registry() -> dict[str, Any]:
    try:
        raw = _REGISTRY_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return dict(_DEFAULT)


def reload_registry() -> None:
    _load_registry.cache_clear()


def _normalize_id(model_id: str) -> str:
    return (model_id or "").strip().lower()


def lookup_model_meta(model_id: str) -> dict[str, Any]:
    """Return registry metadata for a model id (exact match, then pattern)."""
    mid = _normalize_id(model_id)
    if not mid:
        return {"id": model_id, "tier": "unknown", "recommended": False, "reasoning_capable": False}

    reg = _load_registry()
    for entry in reg.get("models") or []:
        if _normalize_id(entry.get("id")) == mid:
            return {**entry, "id": model_id, "match": "exact"}

    for pattern in reg.get("patterns") or []:
        needle = _normalize_id(str(pattern.get("match") or ""))
        if needle and needle in mid:
            return {
                "id": model_id,
                "label": pattern.get("label") or model_id,
                "tier": pattern.get("tier") or "unknown",
                "reasoning_capable": bool(pattern.get("reasoning_capable")),
                "recommended": bool(pattern.get("recommended")),
                "notes": pattern.get("notes") or "",
                "match": "pattern",
                "pattern": needle,
            }

    return {
        "id": model_id,
        "label": model_id,
        "tier": "unknown",
        "recommended": False,
        "reasoning_capable": False,
        "notes": "",
        "match": "none",
    }


def list_registry_entries() -> list[dict[str, Any]]:
    reg = _load_registry()
    out: list[dict[str, Any]] = []
    for entry in reg.get("models") or []:
        if entry.get("id"):
            out.append({**entry, "match": "exact"})
    for pattern in reg.get("patterns") or []:
        if pattern.get("match"):
            out.append({**pattern, "id": pattern.get("match"), "match": "pattern"})
    return out


def enrich_model_ids(model_ids: list[str]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for mid in model_ids:
        key = _normalize_id(mid)
        if not key or key in seen:
            continue
        seen.add(key)
        meta = lookup_model_meta(mid)
        out.append(meta)
    return out


def enrich_models_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach registry metadata to list_all_models / health payloads."""
    ollama = payload.get("ollama") or []
    openrouter = payload.get("openrouter") or []
    if isinstance(ollama, list):
        payload["ollama_meta"] = enrich_model_ids([str(m) for m in ollama])
    if isinstance(openrouter, list):
        payload["openrouter_meta"] = enrich_model_ids([str(m) for m in openrouter])
    payload["registry"] = list_registry_entries()
    for key in ("active_model", "narrator_model", "deep_model", "preferred_model"):
        val = payload.get(key)
        if val:
            payload[f"{key}_meta"] = lookup_model_meta(str(val))
    return payload


def tier_badge(tier: str) -> str:
    t = (tier or "").lower()
    if t == "narrator":
        return "Narrator"
    if t == "deep":
        return "Deep"
    return "Unknown"
