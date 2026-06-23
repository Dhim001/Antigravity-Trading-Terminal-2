"""Interactive Brokers contract resolution for US equities."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CONTRACT_CACHE_PATH = os.path.join(DATA_DIR, "ib_contracts.json")


def stock_contract(symbol: str):
    """Build a SMART-routed US stock contract (qualify before use)."""
    from ib_async import Stock

    sym = symbol.upper().strip()
    return Stock(sym, "SMART", "USD")


def load_contract_cache() -> dict[str, Any]:
    if not os.path.isfile(CONTRACT_CACHE_PATH):
        return {}
    try:
        with open(CONTRACT_CACHE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read IB contract cache: %s", exc)
        return {}


def save_contract_cache(cache: dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONTRACT_CACHE_PATH, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=2, sort_keys=True)


def cache_contract(symbol: str, contract) -> None:
    """Persist conId / exchange after qualifyContracts."""
    sym = symbol.upper()
    cache = load_contract_cache()
    cache[sym] = {
        "conId": int(getattr(contract, "conId", 0) or 0),
        "symbol": getattr(contract, "symbol", sym),
        "exchange": getattr(contract, "exchange", "") or "SMART",
        "primaryExchange": getattr(contract, "primaryExchange", "") or "",
        "currency": getattr(contract, "currency", "USD") or "USD",
    }
    save_contract_cache(cache)
