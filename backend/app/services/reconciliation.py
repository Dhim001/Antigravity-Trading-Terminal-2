"""Track and reconcile ambiguous live order outcomes (at-most-once discipline)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.connection import db_session

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def record_ambiguous_order(
    order_req: dict,
    message: str,
    *,
    bot_id: str | None = None,
    broker: str | None = None,
) -> str:
    """Persist an ambiguous order for operator review."""
    row_id = str(uuid.uuid4())
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO ambiguous_orders
            (id, symbol, side, quantity, order_type, bot_id, broker, payload, message, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                row_id,
                order_req.get("symbol"),
                (order_req.get("side") or "").upper(),
                float(order_req.get("quantity") or 0),
                (order_req.get("type") or "MARKET").upper(),
                bot_id,
                broker,
                json.dumps(order_req),
                message,
                _now_iso(),
            ),
        )
    logger.warning("Ambiguous order recorded: %s %s", row_id, order_req.get("symbol"))
    return row_id


def list_ambiguous_orders(*, include_resolved: bool = False) -> list[dict[str, Any]]:
    with db_session(commit=False) as conn:
        cursor = conn.cursor()
        if include_resolved:
            cursor.execute(
                """
                SELECT id, symbol, side, quantity, order_type, bot_id, broker, message,
                       status, resolution, created_at, resolved_at
                FROM ambiguous_orders
                ORDER BY created_at DESC
                LIMIT 200
                """
            )
        else:
            cursor.execute(
                """
                SELECT id, symbol, side, quantity, order_type, bot_id, broker, message,
                       status, resolution, created_at, resolved_at
                FROM ambiguous_orders
                WHERE status = 'pending'
                ORDER BY created_at DESC
                LIMIT 100
                """
            )
        rows = cursor.fetchall()
    out = []
    for row in rows:
        item = dict(row) if isinstance(row, dict) else {
            "id": row[0], "symbol": row[1], "side": row[2], "quantity": row[3],
            "order_type": row[4], "bot_id": row[5], "broker": row[6], "message": row[7],
            "status": row[8], "resolution": row[9], "created_at": row[10], "resolved_at": row[11],
        }
        out.append(item)
    return out


def resolve_ambiguous_order(order_id: str, resolution: str, note: str = "") -> bool:
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE ambiguous_orders
            SET status = 'resolved', resolution = ?, resolved_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (f"{resolution}:{note}" if note else resolution, _now_iso(), order_id),
        )
        return cursor.rowcount > 0


def auto_reconcile_with_portfolio(oms) -> dict[str, Any]:
    """
    Compare pending ambiguous orders against open positions.
    Marks as 'auto_matched' when a position exists for the symbol (BUY) or
    no position (SELL close).
    """
    pending = list_ambiguous_orders(include_resolved=False)
    if not pending:
        return {"checked": 0, "matched": 0, "still_pending": 0}

    positions: dict[str, float] = {}
    try:
        account = oms.get_account_data()
        for pos in account.get("positions", []):
            sym = pos.get("symbol")
            if sym:
                positions[sym] = float(pos.get("size") or 0)
    except Exception as exc:
        logger.error("Auto-reconcile portfolio read failed: %s", exc)
        return {"checked": len(pending), "matched": 0, "still_pending": len(pending), "error": str(exc)}

    matched = 0
    for item in pending:
        sym = item.get("symbol")
        side = (item.get("side") or "").upper()
        size = positions.get(sym, 0.0)
        auto_match = False
        if side == "BUY" and size > 0:
            auto_match = True
        elif side == "SELL" and abs(size) < 1e-9:
            auto_match = True

        if auto_match and resolve_ambiguous_order(
            item["id"],
            "auto_matched",
            f"position_size={size}",
        ):
            matched += 1

    still = len(list_ambiguous_orders(include_resolved=False))
    return {"checked": len(pending), "matched": matched, "still_pending": still}
