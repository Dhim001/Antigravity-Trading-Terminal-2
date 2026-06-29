"""Massive/Polygon REST provider for dividends, splits, and market holidays."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import MASSIVE_API_KEY, MASSIVE_REST_URL, SYMBOLS
from app.services.altdata.store import upsert_corporate_events, upsert_economic_events
from app.services.massive_symbols import terminal_to_massive_rest_ticker

logger = logging.getLogger(__name__)

_SOURCE = "MASSIVE_POLYGON"


def _equity_symbols(symbols: list[str] | None) -> list[str]:
    from app.services.massive_symbols import is_crypto_terminal_symbol

    syms = symbols or [s for s, info in SYMBOLS.items() if info.get("asset") == "USD"]
    return [s for s in syms if not is_crypto_terminal_symbol(s)]


def _get_json(path: str, params: dict | None = None) -> dict | list | None:
    if not MASSIVE_API_KEY:
        return None
    url = f"{MASSIVE_REST_URL.rstrip('/')}{path}"
    q = dict(params or {})
    q["apiKey"] = MASSIVE_API_KEY
    try:
        with httpx.Client(timeout=25.0) as client:
            resp = client.get(url, params=q)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning("Alt-data REST %s failed: %s", path, exc)
        return None


def fetch_market_holidays() -> list[dict[str, Any]]:
    data = _get_json("/v1/marketstatus/upcoming")
    if not data:
        return []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("results") or data.get("days") or data.get("response") or []
    else:
        items = []
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sched = item.get("date") or item.get("open") or item.get("close") or ""
        name = item.get("name") or item.get("exchange") or "Market status"
        event_id = f"holiday:{sched}:{name}"
        rows.append({
            "event_id": event_id,
            "event_type": "market_holiday",
            "title": str(name),
            "scheduled_at": str(sched),
            "impact": item.get("status"),
            "country": item.get("exchange") or "US",
            "source": _SOURCE,
            "raw": item,
        })
    return rows


def fetch_dividends(symbol: str) -> list[dict[str, Any]]:
    info = SYMBOLS.get(symbol, {})
    ticker = terminal_to_massive_rest_ticker(symbol, info)
    data = _get_json(
        "/v3/reference/dividends",
        {"ticker": ticker, "limit": 50, "order": "desc"},
    )
    if not data:
        return []
    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        return []
    rows: list[dict[str, Any]] = []
    for item in results:
        ex = item.get("ex_dividend_date") or item.get("declaration_date") or ""
        cash = item.get("cash_amount")
        rows.append({
            "id": f"div:{symbol}:{ex}:{cash}",
            "symbol": symbol,
            "event_type": "dividend",
            "event_date": str(ex),
            "title": f"Dividend ${cash}" if cash is not None else "Dividend",
            "metadata": item,
            "source": _SOURCE,
        })
    return rows


def fetch_splits(symbol: str) -> list[dict[str, Any]]:
    info = SYMBOLS.get(symbol, {})
    ticker = terminal_to_massive_rest_ticker(symbol, info)
    data = _get_json(
        "/v3/reference/splits",
        {"ticker": ticker, "limit": 20, "order": "desc"},
    )
    if not data:
        return []
    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        return []
    rows: list[dict[str, Any]] = []
    for item in results:
        ex = item.get("execution_date") or ""
        ratio = f"{item.get('split_from')}:{item.get('split_to')}"
        rows.append({
            "id": f"split:{symbol}:{ex}:{ratio}",
            "symbol": symbol,
            "event_type": "split",
            "event_date": str(ex),
            "title": f"Split {ratio}",
            "metadata": item,
            "source": _SOURCE,
        })
    return rows


def refresh_altdata(symbols: list[str] | None = None) -> dict[str, Any]:
    """Fetch and persist alt-data for configured symbols."""
    syms = _equity_symbols(symbols)
    economic = fetch_market_holidays()
    corporate: list[dict[str, Any]] = []
    for sym in syms:
        corporate.extend(fetch_dividends(sym))
        corporate.extend(fetch_splits(sym))

    econ_written = upsert_economic_events(economic)
    corp_written = upsert_corporate_events(corporate)
    return {
        "economic_fetched": len(economic),
        "economic_written": econ_written,
        "corporate_fetched": len(corporate),
        "corporate_written": corp_written,
        "symbols": len(syms),
    }
