"""Paper margin ledger helpers for simulated long/short positions."""

from __future__ import annotations


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
