"""Bracket and OCO order helpers for simulated OMS."""

from __future__ import annotations

import uuid
from typing import Any


def new_group_id() -> str:
    return str(uuid.uuid4())


def is_bracket_request(order_req: dict) -> bool:
    if order_req.get("bracket") is False:
        return False
    if order_req.get("bracket") is True:
        return True
    return any(
        order_req.get(k) is not None
        for k in (
            "stop_loss_price",
            "take_profit_price",
            "stop_loss_percent",
            "take_profit_percent",
            "trailing_stop_percent",
        )
    )


def resolve_bracket_levels(
    side: str,
    ref_price: float,
    *,
    stop_loss_percent: float | None = None,
    take_profit_percent: float | None = None,
    stop_loss_price: float | None = None,
    take_profit_price: float | None = None,
) -> tuple[float | None, float | None, float | None, float | None]:
    """Return (sl_pct, tp_pct, sl_price, tp_price) for a bracket entry."""
    side = (side or "").upper()
    sl_pct = float(stop_loss_percent) if stop_loss_percent is not None else None
    tp_pct = float(take_profit_percent) if take_profit_percent is not None else None
    sl_price = float(stop_loss_price) if stop_loss_price is not None else None
    tp_price = float(take_profit_price) if take_profit_price is not None else None

    if ref_price <= 0:
        return sl_pct, tp_pct, sl_price, tp_price

    if sl_price is None and sl_pct is not None:
        sl_price = ref_price * (1 - sl_pct / 100) if side == "BUY" else ref_price * (1 + sl_pct / 100)
    if tp_price is None and tp_pct is not None:
        tp_price = ref_price * (1 + tp_pct / 100) if side == "BUY" else ref_price * (1 - tp_pct / 100)

    if sl_price is not None and sl_pct is None:
        sl_pct = round(abs(ref_price - sl_price) / ref_price * 100, 4)
    if tp_price is not None and tp_pct is None:
        tp_pct = round(abs(tp_price - ref_price) / ref_price * 100, 4)

    return sl_pct, tp_pct, sl_price, tp_price


def create_oco_exit_orders(
    cursor,
    *,
    symbol: str,
    quantity: float,
    position_size: float,
    oco_group_id: str,
    order_group_id: str | None,
    stop_loss_price: float | None,
    take_profit_price: float | None,
    parent_order_id: str,
    bot_id: str | None = None,
    signal_id: str | None = None,
) -> list[str]:
    """Insert OCO exit leg rows; return created order ids."""
    if not stop_loss_price and not take_profit_price:
        return []

    exit_side = "SELL" if float(position_size) > 0 else "BUY"

    created: list[str] = []
    legs: list[tuple[str, str, float | None]] = []
    if stop_loss_price is not None:
        legs.append(("STOP", "SL", float(stop_loss_price)))
    if take_profit_price is not None:
        legs.append(("TAKE_PROFIT", "TP", float(take_profit_price)))

    for leg_type, leg_code, trigger_price in legs:
        leg_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO orders (
                id, symbol, type, side, price, quantity, status,
                filled_quantity, average_fill_price,
                stop_loss_percent, take_profit_percent,
                bot_id, signal_id,
                order_group_id, leg_type, oco_group_id,
                stop_loss_price, take_profit_price
            )
            VALUES (?, ?, ?, ?, ?, ?, 'OCO_ACTIVE', 0, 0, NULL, NULL, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                leg_id,
                symbol,
                leg_type,
                exit_side,
                trigger_price,
                quantity,
                bot_id,
                signal_id,
                order_group_id,
                leg_code,
                oco_group_id,
                stop_loss_price if leg_code == "SL" else None,
                take_profit_price if leg_code == "TP" else None,
            ),
        )
        created.append(leg_id)
    return created


def cancel_oco_group(cursor, oco_group_id: str, *, except_leg: str | None = None) -> int:
    if not oco_group_id:
        return 0
    if except_leg:
        cursor.execute(
            """
            UPDATE orders SET status = 'CANCELED'
            WHERE oco_group_id = ? AND status = 'OCO_ACTIVE' AND leg_type != ?
            """,
            (oco_group_id, except_leg),
        )
    else:
        cursor.execute(
            """
            UPDATE orders SET status = 'CANCELED'
            WHERE oco_group_id = ? AND status = 'OCO_ACTIVE'
            """,
            (oco_group_id,),
        )
    return cursor.rowcount if cursor.rowcount is not None else 0


def cancel_oco_for_symbol(cursor, symbol: str) -> int:
    cursor.execute(
        """
        UPDATE orders SET status = 'CANCELED'
        WHERE symbol = ? AND status = 'OCO_ACTIVE'
        """,
        (symbol,),
    )
    return cursor.rowcount if cursor.rowcount is not None else 0


def bracket_result_fields(
    *,
    order_group_id: str | None,
    oco_group_id: str | None,
    bracket: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {"bracket": bracket}
    if order_group_id:
        out["order_group_id"] = order_group_id
    if oco_group_id:
        out["oco_group_id"] = oco_group_id
    return out
