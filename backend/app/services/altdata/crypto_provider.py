"""Binance USD-M futures — funding rate + open interest (public API, no key)."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import CRYPTO_DERIVATIVES_ENABLED, CRYPTO_SYMBOLS
from app.services.altdata.crypto_derivatives import score_derivatives_positioning
from app.services.altdata.store import insert_crypto_derivatives_snapshot
from app.services.massive_symbols import is_crypto_terminal_symbol

logger = logging.getLogger(__name__)

_BINANCE_FAPI = "https://fapi.binance.com"
_SOURCE = "BINANCE_FAPI"


def _binance_symbol(terminal_symbol: str) -> str | None:
    sym = str(terminal_symbol or "").upper().strip()
    if not is_crypto_terminal_symbol(sym):
        return None
    return sym


def _get_json(url: str, params: dict | None = None) -> dict | list | None:
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, params=params or {})
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.debug("Binance %s failed: %s", url, exc)
        return None


def fetch_funding_and_mark(symbol: str) -> dict[str, Any] | None:
    bsym = _binance_symbol(symbol)
    if not bsym:
        return None
    data = _get_json(f"{_BINANCE_FAPI}/fapi/v1/premiumIndex", {"symbol": bsym})
    if not isinstance(data, dict):
        return None
    try:
        return {
            "funding_rate": float(data.get("lastFundingRate") or 0),
            "mark_price": float(data.get("markPrice") or 0),
            "index_price": float(data.get("indexPrice") or 0),
        }
    except (TypeError, ValueError):
        return None


def fetch_open_interest(symbol: str) -> float | None:
    bsym = _binance_symbol(symbol)
    if not bsym:
        return None
    data = _get_json(f"{_BINANCE_FAPI}/fapi/v1/openInterest", {"symbol": bsym})
    if not isinstance(data, dict):
        return None
    try:
        return float(data.get("openInterest") or 0)
    except (TypeError, ValueError):
        return None


def fetch_oi_change_24h_pct(symbol: str) -> float | None:
    """Approximate 24h OI change from 1h history buckets."""
    bsym = _binance_symbol(symbol)
    if not bsym:
        return None
    data = _get_json(
        f"{_BINANCE_FAPI}/futures/data/openInterestHist",
        {"symbol": bsym, "period": "1h", "limit": 25},
    )
    if not isinstance(data, list) or len(data) < 2:
        return None
    try:
        latest = float(data[-1].get("sumOpenInterest") or data[-1].get("sumOpenInterestValue") or 0)
        # ~24h ago bucket
        idx = max(0, len(data) - 25)
        prior = float(data[idx].get("sumOpenInterest") or data[idx].get("sumOpenInterestValue") or 0)
        if prior <= 0 or latest <= 0:
            return None
        return round((latest - prior) / prior * 100.0, 2)
    except (TypeError, ValueError, IndexError):
        return None


def refresh_crypto_derivatives(symbols: list[str] | None = None) -> dict[str, Any]:
    if not CRYPTO_DERIVATIVES_ENABLED:
        return {"enabled": False, "reason": "CRYPTO_DERIVATIVES_ENABLED=false"}

    syms = symbols or list(CRYPTO_SYMBOLS.keys())
    crypto_syms = [s for s in syms if is_crypto_terminal_symbol(s)]
    written = 0
    errors = 0
    now = time.time()

    for sym in crypto_syms:
        try:
            fund = fetch_funding_and_mark(sym)
            oi = fetch_open_interest(sym)
            oi_chg = fetch_oi_change_24h_pct(sym)
            if not fund and oi is None:
                errors += 1
                continue
            fr = fund.get("funding_rate") if fund else None
            score, _, quadrant = score_derivatives_positioning(
                funding_rate=fr,
                oi_change_24h_pct=oi_chg,
            )
            insert_crypto_derivatives_snapshot({
                "symbol": sym,
                "recorded_at": now,
                "funding_rate": fr,
                "open_interest": oi,
                "oi_change_24h_pct": oi_chg,
                "mark_price": fund.get("mark_price") if fund else None,
                "quadrant": quadrant,
                "score": score,
                "source": _SOURCE,
                "metadata": fund or {},
            })
            written += 1
        except Exception as exc:
            logger.warning("Crypto derivatives refresh %s: %s", sym, exc)
            errors += 1

    return {
        "enabled": True,
        "symbols_checked": len(crypto_syms),
        "snapshots_written": written,
        "errors": errors,
    }
