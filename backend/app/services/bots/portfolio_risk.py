"""Portfolio-level exposure limits across all bot positions."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import (
    CORRELATION_GROUPS,
    PORTFOLIO_MAX_GROSS_EXPOSURE_PCT,
    PORTFOLIO_MAX_GROUP_EXPOSURE_PCT,
    CRYPTO_SYMBOLS,
)
from app.database import get_connection


@dataclass
class PortfolioSnapshot:
    account_equity: float
    gross_exposure: float
    group_exposure: dict[str, float]
    symbol_exposure: dict[str, float]


def symbol_correlation_group(symbol: str) -> str:
    for group, members in CORRELATION_GROUPS.items():
        if symbol in members:
            return group
    if symbol.endswith("USDT") or symbol in CRYPTO_SYMBOLS:
        return "CRYPTO"
    if symbol in ("SPY", "QQQ"):
        return "INDEX_ETF"
    return "US_EQUITY"


def _mark_prices(oms, symbols: set[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    feed = getattr(oms, "feed", None)
    for sym in symbols:
        mark = 0.0
        if feed and hasattr(feed, "_symbols") and sym in feed._symbols:
            mark = float(feed._symbols[sym].get("price") or 0)
        if mark <= 0 and feed and hasattr(feed, "get_market_data"):
            md = feed.get_market_data(sym) or {}
            mark = float(md.get("price") or 0)
        if mark > 0:
            prices[sym] = mark
    account = oms.get_account_data()
    for sym, pos in (account.get("positions") or {}).items():
        if sym not in prices and float(pos.get("size") or 0):
            prices[sym] = float(pos.get("avg_price") or pos.get("mark") or 0)
    return prices


def list_bot_exposures() -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT bot_id, symbol, size, avg_price
            FROM bot_positions
            WHERE ABS(size) > 1e-8
            """
        )
        return [
            {
                "bot_id": row["bot_id"],
                "symbol": row["symbol"],
                "size": float(row["size"]),
                "avg_price": float(row["avg_price"]),
            }
            for row in cursor.fetchall()
        ]
    finally:
        conn.close()


def build_portfolio_snapshot(oms) -> PortfolioSnapshot:
    account = oms.get_account_data()
    balances = account.get("balances") or {}
    cash = float(balances.get("USD", {}).get("balance") or balances.get("USDT", {}).get("balance") or 0)

    bot_rows = list_bot_exposures()
    oms_positions = account.get("positions") or {}
    symbols = {r["symbol"] for r in bot_rows} | {s for s, p in oms_positions.items() if float(p.get("size") or 0)}
    marks = _mark_prices(oms, symbols)

    symbol_exposure: dict[str, float] = {}
    for row in bot_rows:
        sym = row["symbol"]
        mark = marks.get(sym) or row["avg_price"]
        symbol_exposure[sym] = symbol_exposure.get(sym, 0.0) + abs(row["size"] * mark)

    for sym, pos in oms_positions.items():
        size = float(pos.get("size") or 0)
        if abs(size) < 1e-8:
            continue
        mark = marks.get(sym) or float(pos.get("avg_price") or 0)
        symbol_exposure[sym] = max(symbol_exposure.get(sym, 0.0), abs(size * mark))

    group_exposure: dict[str, float] = {}
    for sym, exp in symbol_exposure.items():
        grp = symbol_correlation_group(sym)
        group_exposure[grp] = group_exposure.get(grp, 0.0) + exp

    gross = sum(symbol_exposure.values())
    equity = cash + gross
    return PortfolioSnapshot(
        account_equity=max(equity, cash, 1.0),
        gross_exposure=gross,
        group_exposure=group_exposure,
        symbol_exposure=symbol_exposure,
    )


def validate_portfolio_entry(
    snapshot: PortfolioSnapshot,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
) -> tuple[bool, str, float | None]:
    """Return (allowed, reason, capped_quantity). Exits are always allowed."""
    if side != "BUY":
        return True, "OK", quantity

    notional = quantity * price
    if notional <= 0:
        return False, "Invalid notional.", None

    max_gross = snapshot.account_equity * (PORTFOLIO_MAX_GROSS_EXPOSURE_PCT / 100.0)
    projected_gross = snapshot.gross_exposure + notional
    if projected_gross > max_gross:
        headroom = max(0.0, max_gross - snapshot.gross_exposure)
        if headroom < price * 0.001:
            return False, (
                f"Portfolio gross exposure cap ({PORTFOLIO_MAX_GROSS_EXPOSURE_PCT}% of "
                f"${snapshot.account_equity:,.0f} equity) reached."
            ), None
        capped_qty = headroom / price
        return True, f"Capped to portfolio gross limit ({PORTFOLIO_MAX_GROSS_EXPOSURE_PCT}%).", capped_qty

    group = symbol_correlation_group(symbol)
    max_group = snapshot.account_equity * (PORTFOLIO_MAX_GROUP_EXPOSURE_PCT / 100.0)
    projected_group = snapshot.group_exposure.get(group, 0.0) + notional
    if projected_group > max_group:
        headroom = max(0.0, max_group - snapshot.group_exposure.get(group, 0.0))
        if headroom < price * 0.001:
            return False, (
                f"Correlation group '{group}' exposure cap "
                f"({PORTFOLIO_MAX_GROUP_EXPOSURE_PCT}% of equity) reached."
            ), None
        capped_qty = headroom / price
        return True, f"Capped to {group} group limit ({PORTFOLIO_MAX_GROUP_EXPOSURE_PCT}%).", capped_qty

    return True, "OK", quantity
