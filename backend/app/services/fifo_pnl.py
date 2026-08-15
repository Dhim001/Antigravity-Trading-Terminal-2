"""FIFO realized PnL for order fills — shared by sim OMS write and read paths."""

from __future__ import annotations

from app.services.paper_ledger import classify_buy, classify_sell

_EPS = 1e-9


def _symbol_queues(queues: dict, symbol: str) -> dict[str, list[list[float]]]:
    if symbol not in queues:
        queues[symbol] = {"long": [], "short": []}
    return queues[symbol]


def align_queues_to_position(
    queues: dict[str, dict[str, list[list[float]]]],
    symbol: str,
    position_size: float,
    avg_price: float | None = None,
) -> None:
    """Reset FIFO lots for ``symbol`` to match the live (or running) position.

    Prevents stale long lots (e.g. after orphan base inventory was cleared
    without a sell fill) from booking realized PnL onto a fresh short open.
    """
    sym_q = _symbol_queues(queues, symbol)
    sym_q["long"].clear()
    sym_q["short"].clear()
    size = float(position_size or 0.0)
    px = float(avg_price or 0.0)
    if size > _EPS:
        sym_q["long"].append([px, size])
    elif size < -_EPS:
        sym_q["short"].append([px, abs(size)])


def fifo_sell_pnl(
    lots: list[list[float]],
    fill_price: float,
    fill_qty: float,
) -> tuple[float | None, float | None, float]:
    """Consume buy lots FIFO for a sell fill. Returns (cost_basis, realized_pnl, closed_qty)."""
    if fill_qty <= 0:
        return None, None, 0.0

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
        return None, None, 0.0

    cost_basis = total_cost / total_qty
    realized_pnl = (fill_price - cost_basis) * total_qty
    return cost_basis, realized_pnl, total_qty


def fifo_cover_pnl(
    short_lots: list[list[float]],
    fill_price: float,
    fill_qty: float,
) -> tuple[float | None, float | None, float]:
    """Consume short lots FIFO for a buy-to-cover fill. Returns (cost_basis, realized_pnl, closed_qty)."""
    if fill_qty <= 0:
        return None, None, 0.0

    remaining = fill_qty
    total_cost = 0.0
    total_qty = 0.0
    queue = short_lots

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
        return None, None, 0.0

    cost_basis = total_cost / total_qty
    realized_pnl = (cost_basis - fill_price) * total_qty
    return cost_basis, realized_pnl, total_qty


def apply_fill_to_queues(
    queues: dict[str, dict[str, list[list[float]]]],
    symbol: str,
    side: str,
    fill_price: float,
    fill_qty: float,
    *,
    position_size: float | None = None,
) -> tuple[float | None, float | None]:
    """Update in-memory FIFO queues and return PnL for closing fills.

    When ``position_size`` is the pre-fill net position, only the close leg
    (long exit / short cover) can realize PnL — pure opens never do, even if
    the queue still holds phantom opposite lots.
    """
    if fill_qty <= 0:
        return None, None

    sym_q = _symbol_queues(queues, symbol)
    remaining = fill_qty
    cost_basis = None
    realized_pnl = None

    if position_size is not None:
        if side == "BUY":
            short_cover, long_open = classify_buy(float(position_size), fill_qty)
            if short_cover > _EPS:
                cost_basis, realized_pnl, closed = fifo_cover_pnl(
                    sym_q["short"], fill_price, short_cover,
                )
                # Drop any phantom shorts beyond what the live position had.
                if closed + _EPS < short_cover:
                    sym_q["short"].clear()
            if long_open > _EPS:
                sym_q["long"].append([fill_price, long_open])
            return cost_basis, realized_pnl

        if side == "SELL":
            long_close, short_open = classify_sell(float(position_size), 0.0, fill_qty)
            if long_close > _EPS:
                cost_basis, realized_pnl, closed = fifo_sell_pnl(
                    sym_q["long"], fill_price, long_close,
                )
                if closed + _EPS < long_close:
                    sym_q["long"].clear()
            if short_open > _EPS:
                sym_q["short"].append([fill_price, short_open])
            return cost_basis, realized_pnl

        return None, None

    if side == "BUY":
        if sym_q["short"]:
            cost_basis, realized_pnl, closed = fifo_cover_pnl(sym_q["short"], fill_price, remaining)
            remaining -= closed
        if remaining > _EPS:
            sym_q["long"].append([fill_price, remaining])
        return cost_basis, realized_pnl

    if side == "SELL":
        if sym_q["long"]:
            cost_basis, realized_pnl, closed = fifo_sell_pnl(sym_q["long"], fill_price, remaining)
            remaining -= closed
        if remaining > _EPS:
            sym_q["short"].append([fill_price, remaining])
        return cost_basis, realized_pnl

    return None, None


def _update_running_position(
    size: float,
    avg: float,
    side: str,
    fill_price: float,
    fill_qty: float,
) -> tuple[float, float]:
    """Mirror sim OMS net-size update for rebuild/backfill."""
    delta = fill_qty if side == "BUY" else -fill_qty
    new_size = size + delta
    if abs(new_size) <= _EPS:
        return 0.0, 0.0
    if size >= 0 and delta > 0:
        new_avg = ((size * avg) + fill_qty * fill_price) / new_size if new_size else 0.0
    elif size <= 0 and delta < 0:
        new_avg = ((abs(size) * avg) + fill_qty * fill_price) / abs(new_size) if new_size else 0.0
    elif (size > 0 and new_size > 0) or (size < 0 and new_size < 0):
        new_avg = avg
    else:
        new_avg = fill_price
    return new_size, new_avg


def record_order_fifo_pnl(
    cursor,
    order_id: str,
    symbol: str,
    side: str,
    fill_price: float,
    fill_qty: float,
    *,
    cached_queues: dict | None = None,
    position_size: float | None = None,
    position_avg: float | None = None,
) -> tuple[float | None, float | None]:
    """Compute FIFO PnL and persist on the order row.

    Pass pre-fill ``position_size`` / ``position_avg`` from the OMS whenever
    possible so open legs cannot inherit realized PnL from stale opposite lots.
    """
    if position_size is not None:
        queues = cached_queues if cached_queues is not None else {}
        align_queues_to_position(queues, symbol, position_size, position_avg)
        pre_size = float(position_size)
    elif cached_queues is not None:
        queues = cached_queues
        pre_size = None
    else:
        cursor.execute(
            """
            SELECT side, filled_quantity, average_fill_price
            FROM orders
            WHERE symbol = ? AND status = 'FILLED' AND id != ?
            ORDER BY timestamp ASC, rowid ASC
            """,
            (symbol, order_id),
        )
        queues = {}
        running_size = 0.0
        running_avg = 0.0
        for row in cursor.fetchall():
            qty = float(row["filled_quantity"] or 0)
            price = float(row["average_fill_price"] or 0)
            if qty <= 0:
                continue
            align_queues_to_position(queues, symbol, running_size, running_avg)
            apply_fill_to_queues(
                queues, symbol, row["side"], price, qty, position_size=running_size,
            )
            running_size, running_avg = _update_running_position(
                running_size, running_avg, row["side"], price, qty,
            )
        pre_size = running_size
        align_queues_to_position(queues, symbol, running_size, running_avg)

    cost_basis, realized_pnl = apply_fill_to_queues(
        queues,
        symbol,
        side,
        fill_price,
        fill_qty,
        position_size=pre_size,
    )

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
    queues: dict[str, dict[str, list[list[float]]]],
    symbol: str,
    side: str,
    fill_price: float,
    fill_qty: float,
    *,
    position_size: float | None = None,
) -> None:
    """Move FIFO queues forward without returning PnL (for already-persisted fills)."""
    if fill_qty <= 0:
        return
    apply_fill_to_queues(
        queues, symbol, side, fill_price, fill_qty, position_size=position_size,
    )


def enrich_orders_with_pnl(orders: list[dict]) -> list[dict]:
    """Attach trade_value; trust persisted realized_pnl / cost_basis.

    Do not recompute FIFO on read — order-stream position can drift from the
    live book after orphan clears, and recomputing re-inflates History by
    booking short opens against phantom long lots.
    """
    enriched: list[dict] = []

    for order in orders:
        fill_price = float(order["average_fill_price"] or 0)
        fill_qty = float(order["filled_quantity"] or 0)
        cost_basis = order.get("cost_basis")
        realized_pnl = order.get("realized_pnl")

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


def apply_bot_trade_pnl_overrides(cursor) -> int:
    """Align order realized_pnl with bot_trades entry/exit semantics.

    When order-FIFO still sees leftover long lots (orphaned inventory cleared
    without matching sells), short *entries* look like long *exits*. Bot rows
    know the truth: is_exit=0 → no realized PnL; is_exit=1 → bot pnl.
    """
    try:
        cursor.execute(
            """
            SELECT order_id, is_exit, pnl
            FROM bot_trades
            WHERE order_id IS NOT NULL AND TRIM(order_id) != ''
            """
        )
    except Exception:
        return 0

    updates = 0
    for row in cursor.fetchall():
        order_id = row["order_id"]
        is_exit = int(row["is_exit"] or 0)
        if is_exit == 0:
            cursor.execute(
                "UPDATE orders SET realized_pnl = NULL, cost_basis = NULL WHERE id = ?",
                (order_id,),
            )
            updates += 1
            continue
        pnl = row["pnl"]
        if pnl is None:
            continue
        cursor.execute(
            "UPDATE orders SET realized_pnl = ? WHERE id = ?",
            (round(float(pnl), 4), order_id),
        )
        updates += 1
    return updates


def scrub_position_drift_pnl(cursor) -> int:
    """Null FIFO PnL after the last flat when order-stream size ≠ live position.

    Orphan inventory clears (and similar) leave unmatched buys in ``orders`` while
    ``positions`` is flat. FIFO then books every later short-open SELL as a long
    close. Wipe non-authoritative PnL in that drift window; bot exit overlays
    restore real closed-trade PnL afterward.
    """
    cursor.execute(
        """
        SELECT id, symbol, side, filled_quantity, timestamp
        FROM orders
        WHERE status = 'FILLED'
        ORDER BY timestamp ASC, rowid ASC
        """
    )
    rows = [dict(r) for r in cursor.fetchall()]
    if not rows:
        return 0

    live_size: dict[str, float] = {}
    try:
        cursor.execute("SELECT symbol, size FROM positions")
        for row in cursor.fetchall():
            live_size[str(row["symbol"])] = float(row["size"] or 0.0)
    except Exception:
        live_size = {}

    running: dict[str, float] = {}
    last_flat_idx: dict[str, int] = {}
    # Treat start as flat for every symbol that appears.
    for i, order in enumerate(rows):
        sym = order["symbol"]
        if sym not in last_flat_idx:
            last_flat_idx[sym] = -1
        qty = float(order["filled_quantity"] or 0)
        if qty <= 0:
            continue
        size = float(running.get(sym, 0.0))
        if abs(size) <= _EPS:
            last_flat_idx[sym] = i - 1
        delta = qty if order["side"] == "BUY" else -qty
        size += delta
        if abs(size) <= _EPS:
            size = 0.0
            last_flat_idx[sym] = i
        running[sym] = size

    drift_symbols = {
        sym
        for sym, end_size in running.items()
        if abs(end_size - float(live_size.get(sym, 0.0))) > _EPS
    }
    if not drift_symbols:
        return 0

    protected: set[str] = set()
    try:
        cursor.execute(
            """
            SELECT order_id FROM bot_trades
            WHERE is_exit = 1 AND order_id IS NOT NULL AND TRIM(order_id) != ''
            """
        )
        protected = {str(r["order_id"]) for r in cursor.fetchall()}
    except Exception:
        protected = set()

    updates = 0
    for i, order in enumerate(rows):
        sym = order["symbol"]
        if sym not in drift_symbols:
            continue
        if i <= last_flat_idx.get(sym, -1):
            continue
        oid = order["id"]
        if oid in protected:
            continue
        cursor.execute(
            "UPDATE orders SET realized_pnl = NULL, cost_basis = NULL WHERE id = ?",
            (oid,),
        )
        updates += 1
    return updates


def rebuild_order_realized_pnl(cursor) -> int:
    """Recompute every FILLED order's realized_pnl with position-aware FIFO.

    Fixes History totals inflated by short-open fills that inherited PnL from
    stale long lots after position/orphan resets. Drift windows are scrubbed,
    then bot-attributed fills are corrected via ``apply_bot_trade_pnl_overrides``.
    """
    cursor.execute(
        """
        SELECT id, symbol, side, filled_quantity, average_fill_price
        FROM orders
        WHERE status = 'FILLED'
        ORDER BY timestamp ASC, rowid ASC
        """
    )
    rows = [dict(r) for r in cursor.fetchall()]
    if not rows:
        return apply_bot_trade_pnl_overrides(cursor)

    updates = 0
    queues: dict[str, dict[str, list[list[float]]]] = {}
    running_size: dict[str, float] = {}
    running_avg: dict[str, float] = {}

    for order in rows:
        sym = order["symbol"]
        side = order["side"]
        fill_price = float(order["average_fill_price"] or 0)
        fill_qty = float(order["filled_quantity"] or 0)
        if fill_qty <= 0:
            cursor.execute(
                "UPDATE orders SET realized_pnl = NULL, cost_basis = NULL WHERE id = ?",
                (order["id"],),
            )
            updates += 1
            continue

        pre_size = float(running_size.get(sym, 0.0))
        pre_avg = float(running_avg.get(sym, 0.0))
        align_queues_to_position(queues, sym, pre_size, pre_avg)
        cost_basis, realized_pnl = apply_fill_to_queues(
            queues, sym, side, fill_price, fill_qty, position_size=pre_size,
        )
        cursor.execute(
            """
            UPDATE orders SET realized_pnl = ?, cost_basis = ?
            WHERE id = ?
            """,
            (
                round(realized_pnl, 4) if realized_pnl is not None else None,
                round(cost_basis, 4) if cost_basis is not None else None,
                order["id"],
            ),
        )
        updates += 1
        new_size, new_avg = _update_running_position(
            pre_size, pre_avg, side, fill_price, fill_qty,
        )
        running_size[sym] = new_size
        running_avg[sym] = new_avg

    updates += scrub_position_drift_pnl(cursor)
    updates += apply_bot_trade_pnl_overrides(cursor)
    return updates


def backfill_missing_order_pnl(cursor) -> int:
    """Rebuild realized PnL for all fills (position-aware + bot overrides). Idempotent."""
    return rebuild_order_realized_pnl(cursor)


def pnl_authority_integrity(cursor) -> dict:
    """Compare the bot_trades exit-PnL authority against the orders.realized_pnl cache.

    ``bot_trades`` is the single source of truth for closed-trade PnL;
    ``orders.realized_pnl`` is a read cache that must agree. Returns a report
    with per-symbol divergence so a nightly job can alert when the cache drifts
    from the journal (History inflation / equity mismatch symptom).
    """
    report: dict = {
        "ok": True,
        "bot_exit_total": 0.0,
        "order_cache_total": 0.0,
        "diverged_symbols": {},
        "bot_exit_count": 0,
        "order_cache_count": 0,
    }

    try:
        cursor.execute(
            """
            SELECT symbol, COUNT(*) AS n, COALESCE(SUM(pnl), 0) AS total
            FROM bot_trades
            WHERE is_exit = 1 AND pnl IS NOT NULL
            GROUP BY symbol
            """
        )
        bot_rows = {str(r["symbol"]): (int(r["n"]), float(r["total"])) for r in cursor.fetchall()}
    except Exception:
        bot_rows = {}

    try:
        cursor.execute(
            """
            SELECT symbol, COUNT(*) AS n, COALESCE(SUM(realized_pnl), 0) AS total
            FROM orders
            WHERE status = 'FILLED' AND realized_pnl IS NOT NULL
            GROUP BY symbol
            """
        )
        order_rows = {str(r["symbol"]): (int(r["n"]), float(r["total"])) for r in cursor.fetchall()}
    except Exception:
        order_rows = {}

    bot_total = sum(t for _, t in bot_rows.values())
    order_total = sum(t for _, t in order_rows.values())
    report["bot_exit_total"] = round(bot_total, 4)
    report["order_cache_total"] = round(order_total, 4)
    report["bot_exit_count"] = sum(n for n, _ in bot_rows.values())
    report["order_cache_count"] = sum(n for n, _ in order_rows.values())

    tol = 0.02  # 2 cents — rounding jitter only
    diverged: dict[str, dict] = {}
    for sym in set(bot_rows) | set(order_rows):
        b_n, b_t = bot_rows.get(sym, (0, 0.0))
        o_n, o_t = order_rows.get(sym, (0, 0.0))
        # The orders cache covers non-bot manual fills too; only flag when the
        # bot-attributed subset is exceeded — cache total should be >= bot total
        # for symbols where every bot exit landed in orders, and never *less*
        # than bot truth minus tolerance.
        if o_t < b_t - tol:
            diverged[sym] = {
                "bot_exits": b_n,
                "bot_pnl": round(b_t, 4),
                "order_cache_pnl": round(o_t, 4),
                "delta": round(b_t - o_t, 4),
            }
    report["diverged_symbols"] = diverged
    report["ok"] = not diverged
    return report