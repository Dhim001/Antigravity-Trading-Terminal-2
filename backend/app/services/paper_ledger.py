"""Paper margin ledger helpers for simulated long/short positions."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

QUOTE_ASSETS: tuple[str, ...] = ("USD", "USDT")
_EPS = 1e-8


def classify_sell(
    position_size: float,
    locked_sell_qty: float,
    quantity: float,
) -> tuple[float, float]:
    """Return (long_close_qty, short_open_qty) for a SELL order."""
    long_available = max(0.0, position_size - locked_sell_qty) if position_size > 0 else 0.0
    long_close_qty = min(quantity, long_available)
    short_open_qty = quantity - long_close_qty
    return long_close_qty, short_open_qty


def classify_buy(position_size: float, quantity: float) -> tuple[float, float]:
    """Return (short_cover_qty, long_open_qty) for a BUY order."""
    short_available = max(0.0, -position_size) if position_size < 0 else 0.0
    short_cover_qty = min(quantity, short_available)
    long_open_qty = quantity - short_cover_qty
    return short_cover_qty, long_open_qty


def short_margin_required(
    position_size: float,
    locked_sell_qty: float,
    quantity: float,
    price: float,
) -> float:
    """Quote collateral required to open/increase a short on this sell."""
    _, short_open_qty = classify_sell(position_size, locked_sell_qty, quantity)
    return short_open_qty * price


def apply_fill_balances(
    cursor,
    *,
    side: str,
    price: float,
    quantity: float,
    quote: str,
    base_asset: str,
    position_size: float,
    position_avg: float,
) -> None:
    """Update account balances for a fill given the pre-fill net position."""
    if side == "BUY":
        short_cover, long_open = classify_buy(position_size, quantity)

        if short_cover > 0:
            cover_value = price * short_cover
            margin_release = position_avg * short_cover
            cursor.execute(
                "UPDATE accounts SET balance = balance - ? WHERE asset = ?",
                (cover_value, quote),
            )
            cursor.execute(
                "UPDATE accounts SET locked = MAX(0.0, locked - ?) WHERE asset = ?",
                (margin_release, quote),
            )

        if long_open > 0:
            long_value = price * long_open
            cursor.execute(
                "UPDATE accounts SET balance = balance - ? WHERE asset = ?",
                (long_value, quote),
            )
            cursor.execute(
                "UPDATE accounts SET balance = balance + ? WHERE asset = ?",
                (long_open, base_asset),
            )
        return

    long_close, short_open = classify_sell(position_size, 0.0, quantity)

    if long_close > 0:
        close_value = price * long_close
        cursor.execute(
            "UPDATE accounts SET balance = balance + ? WHERE asset = ?",
            (close_value, quote),
        )
        cursor.execute(
            "UPDATE accounts SET balance = MAX(0.0, balance - ?) WHERE asset = ?",
            (long_close, base_asset),
        )

    if short_open > 0:
        margin_value = price * short_open
        cursor.execute(
            "UPDATE accounts SET locked = locked + ? WHERE asset = ?",
            (margin_value, quote),
        )


def _asset_symbol_index(
    symbol_meta: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, list[str]]:
    """Map base asset → terminal symbols (e.g. ADA → [ADAUSDT])."""
    index: dict[str, list[str]] = {}
    if not symbol_meta:
        return index
    for symbol, meta in symbol_meta.items():
        if not isinstance(meta, Mapping):
            continue
        asset = str(meta.get("asset") or "").upper()
        if not asset or asset in QUOTE_ASSETS:
            continue
        index.setdefault(asset, []).append(str(symbol))
    return index


def reconcile_base_inventories(
    cursor,
    symbol_meta: Mapping[str, Mapping[str, Any]] | None,
    *,
    quote_assets: Sequence[str] = QUOTE_ASSETS,
) -> list[str]:
    """Align non-quote ``accounts.balance`` with aggregate long position size.

    Paper fills credit base inventory (ADA, BTC, …) alongside ``positions``.
    If a position is zeroed without a matching sell (manual cash restore, bad
    SQL, interrupted fill), orphan base rows remain — visible in Balances but
    not closable in Positions. This sync forces:

        accounts[base].balance = Σ max(0, positions[symbol].size)

    Returns assets whose balances were corrected.
    """
    quotes = {str(q).upper() for q in quote_assets}
    asset_symbols = _asset_symbol_index(symbol_meta)

    cursor.execute("SELECT asset, balance FROM accounts")
    account_rows = list(cursor.fetchall())
    corrected: list[str] = []

    seen: set[str] = set()
    for row in account_rows:
        asset = str(row["asset"] if isinstance(row, Mapping) else row[0]).upper()
        if asset in quotes:
            continue
        seen.add(asset)
        symbols = asset_symbols.get(asset, [])
        long_qty = 0.0
        for symbol in symbols:
            cursor.execute(
                "SELECT size FROM positions WHERE symbol = ?",
                (symbol,),
            )
            pos = cursor.fetchone()
            if not pos:
                continue
            size = float(pos["size"] if isinstance(pos, Mapping) else pos[0] or 0)
            if size > _EPS:
                long_qty += size

        current = float(row["balance"] if isinstance(row, Mapping) else row[1] or 0)
        if abs(current - long_qty) <= _EPS:
            continue
        cursor.execute(
            "UPDATE accounts SET balance = ? WHERE asset = ?",
            (long_qty, asset),
        )
        corrected.append(asset)

    # Bases present in the symbol map but missing from accounts (rare).
    for asset, symbols in asset_symbols.items():
        if asset in seen or asset in quotes:
            continue
        long_qty = 0.0
        for symbol in symbols:
            cursor.execute(
                "SELECT size FROM positions WHERE symbol = ?",
                (symbol,),
            )
            pos = cursor.fetchone()
            if not pos:
                continue
            size = float(pos["size"] if isinstance(pos, Mapping) else pos[0] or 0)
            if size > _EPS:
                long_qty += size
        if long_qty <= _EPS:
            continue
        cursor.execute(
            "INSERT INTO accounts (asset, balance, locked) VALUES (?, ?, 0.0)",
            (asset, long_qty),
        )
        corrected.append(asset)

    return corrected
