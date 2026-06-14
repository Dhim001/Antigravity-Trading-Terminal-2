"""FIFO realized PnL for order fills — shared by sim OMS write and read paths."""

from __future__ import annotations


def fifo_sell_pnl(lots: list[list[float]], fill_price: float, fill_qty: float) -> tuple[float | None, float | None]:
    """Consume buy lots FIFO for a sell fill. Returns (cost_basis, realized_pnl)."""
    if fill_qty <= 0:
        return None, None

    remaining = fill_qty
    total_cost = 0.0
    total_qty = 0.0
    queue = lots

    while remaining > 1e-9 and queue:
        lot_price, lot_qty = queue[0]
        used = min(lot_qty, remaining)
        total_cost += lot_price * used
        total_qty += used
        remaining -= used
        lot_qty -= used
        if lot_qty < 1e-9:
            queue.pop(0)
        else:
            queue[0][1] = lot_qty

    if total_qty <= 0:
        return None, None

    cost_basis = total_cost / total_qty
    realized_pnl = (fill_price - cost_basis) * fill_qty
    return cost_basis, realized_pnl


def apply_fill_to_queues(
    queues: dict[str, list[list[float]]],
    symbol: str,
    side: str,
    fill_price: float,
    fill_qty: float,
) -> tuple[float | None, float | None]:
    """Update in-memory FIFO queues and return PnL for closing sells."""
    if fill_qty <= 0:
        return None, None

    if symbol not in queues:
        queues[symbol] = []

    if side == "BUY":
        queues[symbol].append([fill_price, fill_qty])
        return None, None

    if side == "SELL":
        return fifo_sell_pnl(queues[symbol], fill_price, fill_qty)

    return None, None


def record_order_fifo_pnl(
    cursor,
    order_id: str,
    symbol: str,
    side: str,
    fill_price: float,
    fill_qty: float,
) -> tuple[float | None, float | None]:
    """Compute FIFO PnL from prior fills for symbol and persist on the order row."""
    cursor.execute(
        """
        SELECT side, filled_quantity, average_fill_price
        FROM orders
        WHERE symbol = ? AND status = 'FILLED' AND id != ?
        ORDER BY timestamp ASC, id ASC
        """,
        (symbol, order_id),
    )
    queues: dict[str, list[list[float]]] = {symbol: []}
    for row in cursor.fetchall():
        qty = float(row["filled_quantity"] or 0)
        price = float(row["average_fill_price"] or 0)
        if qty <= 0:
            continue
        apply_fill_to_queues(queues, symbol, row["side"], price, qty)

    cost_basis, realized_pnl = apply_fill_to_queues(queues, symbol, side, fill_price, fill_qty)

    cursor.execute(
        """
        UPDATE orders
        SET realized_pnl = ?, cost_basis = ?
        WHERE id = ?
        """,
        (
            round(realized_pnl, 4) if realized_pnl is not None else None,
            round(cost_basis, 4) if cost_basis is not None else None,
            order_id,
        ),
    )
    return cost_basis, realized_pnl


def advance_fifo_queue(
    queues: dict[str, list[list[float]]],
    symbol: str,
    side: str,
    fill_price: float,
    fill_qty: float,
) -> None:
    """Move FIFO queues forward without returning PnL (for already-persisted fills)."""
    if fill_qty <= 0:
        return
    if symbol not in queues:
        queues[symbol] = []
    if side == "BUY":
        queues[symbol].append([fill_price, fill_qty])
    elif side == "SELL":
        fifo_sell_pnl(queues[symbol], fill_price, fill_qty)


def enrich_orders_with_pnl(orders: list[dict]) -> list[dict]:
    """Attach trade_value and PnL fields; replay FIFO only for legacy NULL sells."""
    queues: dict[str, list[list[float]]] = {}
    enriched: list[dict] = []

    for order in orders:
        sym = order["symbol"]
        side = order["side"]
        fill_price = float(order["average_fill_price"] or 0)
        fill_qty = float(order["filled_quantity"] or 0)

        cost_basis = order.get("cost_basis")
        realized_pnl = order.get("realized_pnl")

        if fill_qty > 0:
            if side == "SELL" and realized_pnl is None:
                cost_basis, realized_pnl = apply_fill_to_queues(
                    queues, sym, side, fill_price, fill_qty,
                )
            else:
                advance_fifo_queue(queues, sym, side, fill_price, fill_qty)

        trade_value = fill_price * fill_qty if fill_qty > 0 else (
            float(order.get("price") or 0) * float(order.get("quantity") or 0)
        )

        enriched.append({
            **order,
            "realized_pnl": round(float(realized_pnl), 4) if realized_pnl is not None else None,
            "cost_basis": round(float(cost_basis), 4) if cost_basis is not None else None,
            "trade_value": round(trade_value, 4),
        })

    return enriched


def backfill_missing_order_pnl(cursor) -> int:
    """Persist FIFO PnL for legacy FILLED sells missing stored values."""
    cursor.execute(
        """
        SELECT id, symbol, side, filled_quantity, average_fill_price, realized_pnl
        FROM orders
        WHERE status = 'FILLED'
        ORDER BY timestamp ASC, id ASC
        """
    )
    rows = [dict(r) for r in cursor.fetchall()]
    if not rows:
        return 0

    updates = 0
    queues: dict[str, list[list[float]]] = {}
    for order in rows:
        sym = order["symbol"]
        side = order["side"]
        fill_price = float(order["average_fill_price"] or 0)
        fill_qty = float(order["filled_quantity"] or 0)
        if fill_qty <= 0:
            continue

        if order["realized_pnl"] is not None:
            apply_fill_to_queues(queues, sym, side, fill_price, fill_qty)
            continue

        cost_basis, realized_pnl = apply_fill_to_queues(queues, sym, side, fill_price, fill_qty)
        if side == "SELL" and realized_pnl is not None:
            cursor.execute(
                """
                UPDATE orders SET realized_pnl = ?, cost_basis = ?
                WHERE id = ?
                """,
                (
                    round(realized_pnl, 4),
                    round(cost_basis, 4) if cost_basis is not None else None,
                    order["id"],
                ),
            )
            updates += 1

    return updates
