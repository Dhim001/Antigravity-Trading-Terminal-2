"""Quote-aware cash helpers for the dual USD/USDT paper ledger.

SimOMS / LIVE_* fallback seeds **both** ``USD`` (equities) and ``USDT``
(crypto). Call sites must pick the quote bucket for a trade symbol — never
"first of USD or USDT" — and use the **sum** of quote buckets for portfolio-
level equity / available-funds totals.
"""

from __future__ import annotations

from typing import Any, Mapping

QUOTE_ASSETS: tuple[str, ...] = ("USD", "USDT")


def quote_asset_for_symbol(symbol: str | None) -> str:
    """Return ``USDT`` for crypto terminal symbols, else ``USD``."""
    if not symbol:
        return "USD"
    return "USDT" if "USDT" in str(symbol).upper() else "USD"


def resolve_quote_asset(
    balances: Mapping[str, Any] | None,
    symbol: str | None,
    *,
    quote: str | None = None,
) -> str:
    """Pick the ledger bucket for ``symbol``.

    Prefer the symbol's native quote (USDT crypto / USD equity). If that key is
    **absent** (Alpaca live is USD-only even for BTCUSDT), fall back to the
    other quote asset. Never fall back when both ledgers exist — dual paper
    accounts must debit the correct bucket.
    """
    preferred = quote or quote_asset_for_symbol(symbol)
    if not balances:
        return preferred
    if preferred in balances:
        return preferred
    alt = "USD" if preferred == "USDT" else "USDT"
    if alt in balances:
        return alt
    return preferred


def _row(balances: Mapping[str, Any] | None, asset: str) -> tuple[float, float]:
    if not balances:
        return 0.0, 0.0
    row = balances.get(asset)
    if not isinstance(row, dict):
        return 0.0, 0.0
    try:
        balance = float(row.get("balance") or 0)
    except (TypeError, ValueError):
        balance = 0.0
    try:
        locked = float(row.get("locked") or 0)
    except (TypeError, ValueError):
        locked = 0.0
    return balance, locked


def cash_balance(balances: Mapping[str, Any] | None, quote: str) -> float:
    return _row(balances, quote)[0]


def cash_locked(balances: Mapping[str, Any] | None, quote: str) -> float:
    return _row(balances, quote)[1]


def cash_available(balances: Mapping[str, Any] | None, quote: str) -> float:
    balance, locked = _row(balances, quote)
    return max(0.0, balance - locked)


def cash_for_symbol(
    balances: Mapping[str, Any] | None,
    symbol: str | None,
    *,
    quote: str | None = None,
) -> tuple[float, float, float]:
    """Return ``(balance, locked, available)`` for the symbol's quote asset."""
    q = resolve_quote_asset(balances, symbol, quote=quote)
    balance, locked = _row(balances, q)
    return balance, locked, max(0.0, balance - locked)


def total_quote_balance(balances: Mapping[str, Any] | None) -> float:
    """Sum of USD + USDT balances (portfolio equity cash component)."""
    return sum(cash_balance(balances, asset) for asset in QUOTE_ASSETS)


def total_quote_available(balances: Mapping[str, Any] | None) -> float:
    """Sum of USD + USDT available (unlocked) cash — UI / portfolio funds."""
    return sum(cash_available(balances, asset) for asset in QUOTE_ASSETS)
