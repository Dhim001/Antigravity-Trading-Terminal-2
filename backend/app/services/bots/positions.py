"""Per-bot position slices (account positions remain aggregated in OMS)."""

from __future__ import annotations

from app.database import get_connection

_EPS = 1e-8


def _risk_prices(
    size: float,
    avg_price: float,
    *,
    stop_loss_percent: float | None = None,
    take_profit_percent: float | None = None,
    stop_loss_price: float | None = None,
    take_profit_price: float | None = None,
) -> tuple[float | None, float | None, float | None, float | None]:
    """Compute absolute SL/TP prices from percents or explicit targets."""
    sl_pct = stop_loss_percent
    tp_pct = take_profit_percent
    sl_price = stop_loss_price
    tp_price = take_profit_price

    if size > 0:
        if sl_pct is not None and sl_price is None:
            sl_price = avg_price * (1 - sl_pct / 100)
        if tp_price is not None:
            tp_pct = None
        elif tp_pct is not None:
            tp_price = avg_price * (1 + tp_pct / 100)
    elif size < 0:
        if sl_pct is not None and sl_price is None:
            sl_price = avg_price * (1 + sl_pct / 100)
        if tp_price is not None:
            tp_pct = None
        elif tp_pct is not None:
            tp_price = avg_price * (1 - tp_pct / 100)

    return sl_pct, tp_pct, sl_price, tp_price


def evaluate_risk_trigger(
    size: float,
    avg_price: float,
    market_price: float,
    *,
    stop_loss_percent: float | None,
    take_profit_percent: float | None,
    stop_loss_price: float | None,
    take_profit_price: float | None,
) -> tuple[str | None, float | None]:
    """
    Returns (trigger_type 'SL'|'TP'|None, updated trailing stop_loss_price).
    """
    if abs(size) <= _EPS:
        return None, None

    sl_price = stop_loss_price
    tp_price = take_profit_price
    trigger_type = None

    if size > 0:
        if stop_loss_percent is not None:
            potential_sl = market_price * (1 - stop_loss_percent / 100)
            if sl_price is None or potential_sl > sl_price:
                sl_price = potential_sl
        if sl_price is not None and market_price <= sl_price:
            trigger_type = "SL"
        elif tp_price is not None and market_price >= tp_price:
            trigger_type = "TP"
    else:
        if stop_loss_percent is not None:
            potential_sl = market_price * (1 + stop_loss_percent / 100)
            if sl_price is None or potential_sl < sl_price:
                sl_price = potential_sl
        if sl_price is not None and market_price >= sl_price:
            trigger_type = "SL"
        elif tp_price is not None and market_price <= tp_price:
            trigger_type = "TP"

    trailing_sl = sl_price if stop_loss_percent is not None else None
    return trigger_type, trailing_sl


def get_bot_position(bot_id: str, symbol: str) -> dict:
    """Return bot-local size/avg/risk for risk and snapshot math."""
    if not bot_id or not symbol:
        return {
            "size": 0.0,
            "avg_price": 0.0,
            "stop_loss_percent": None,
            "take_profit_percent": None,
            "stop_loss_price": None,
            "take_profit_price": None,
        }
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT size, avg_price, stop_loss_percent, take_profit_percent,
                   stop_loss_price, take_profit_price
            FROM bot_positions WHERE bot_id = ? AND symbol = ?
            """,
            (bot_id, symbol),
        )
        row = cursor.fetchone()
        if not row:
            return {
                "size": 0.0,
                "avg_price": 0.0,
                "stop_loss_percent": None,
                "take_profit_percent": None,
                "stop_loss_price": None,
                "take_profit_price": None,
            }
        return {
            "size": float(row["size"]),
            "avg_price": float(row["avg_price"]),
            "stop_loss_percent": row["stop_loss_percent"],
            "take_profit_percent": row["take_profit_percent"],
            "stop_loss_price": row["stop_loss_price"],
            "take_profit_price": row["take_profit_price"],
        }
    finally:
        conn.close()


def get_bot_size(bot_id: str, symbol: str) -> float:
    if not bot_id or not symbol:
        return 0.0
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT size FROM bot_positions WHERE bot_id = ? AND symbol = ?",
            (bot_id, symbol),
        )
        row = cursor.fetchone()
        return float(row["size"] if row else 0.0)
    finally:
        conn.close()


def list_owners_grouped() -> dict[str, list[dict]]:
    """All bot position slices keyed by symbol (single query)."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT bot_id, symbol, size, avg_price,
                   stop_loss_percent, take_profit_percent, stop_loss_price, take_profit_price
            FROM bot_positions
            WHERE ABS(size) > ?
            ORDER BY symbol, ABS(size) DESC
            """,
            (_EPS,),
        )
        grouped: dict[str, list[dict]] = {}
        for row in cursor.fetchall():
            sym = row["symbol"]
            grouped.setdefault(sym, []).append({
                "bot_id": row["bot_id"],
                "size": float(row["size"]),
                "avg_price": float(row["avg_price"]),
                "stop_loss_percent": row["stop_loss_percent"],
                "take_profit_percent": row["take_profit_percent"],
                "stop_loss_price": row["stop_loss_price"],
                "take_profit_price": row["take_profit_price"],
            })
        return grouped
    finally:
        conn.close()


def get_symbol_owners(symbol: str) -> list[dict]:
    if not symbol:
        return []
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT bot_id, size, avg_price,
                   stop_loss_percent, take_profit_percent, stop_loss_price, take_profit_price
            FROM bot_positions
            WHERE symbol = ? AND ABS(size) > ?
            ORDER BY ABS(size) DESC
            """,
            (symbol, _EPS),
        )
        return [
            {
                "bot_id": row["bot_id"],
                "size": float(row["size"]),
                "avg_price": float(row["avg_price"]),
                "stop_loss_percent": row["stop_loss_percent"],
                "take_profit_percent": row["take_profit_percent"],
                "stop_loss_price": row["stop_loss_price"],
                "take_profit_price": row["take_profit_price"],
            }
            for row in cursor.fetchall()
        ]
    finally:
        conn.close()


def owners_for_account_payload(symbol: str) -> list[dict]:
    """Lightweight owner list for wire payloads."""
    return [
        {"bot_id": o["bot_id"], "size": o["size"]}
        for o in get_symbol_owners(symbol)
    ]


def update_bot_risk(
    bot_id: str,
    symbol: str,
    avg_price: float,
    side: str,
    *,
    stop_loss_percent: float | None = None,
    take_profit_percent: float | None = None,
    stop_loss_price: float | None = None,
    take_profit_price: float | None = None,
) -> None:
    """Set per-bot virtual SL/TP on an open slice."""
    if not bot_id or not symbol:
        return

    pos = get_bot_position(bot_id, symbol)
    size = pos["size"]
    if abs(size) <= _EPS:
        return

    sl_pct, tp_pct, sl_price, tp_price = _risk_prices(
        size,
        avg_price if avg_price else pos["avg_price"],
        stop_loss_percent=stop_loss_percent,
        take_profit_percent=take_profit_percent,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
    )

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE bot_positions
            SET stop_loss_percent = ?, take_profit_percent = ?,
                stop_loss_price = ?, take_profit_price = ?
            WHERE bot_id = ? AND symbol = ?
            """,
            (sl_pct, tp_pct, sl_price, tp_price, bot_id, symbol),
        )
        conn.commit()
    finally:
        conn.close()


def apply_fill(
    bot_id: str,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    *,
    risk: dict | None = None,
) -> None:
    """Update bot-local position after a fill attributed to bot_id."""
    if not bot_id or not symbol or quantity <= 0:
        return

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT size, avg_price, stop_loss_percent, take_profit_percent,
                   stop_loss_price, take_profit_price
            FROM bot_positions WHERE bot_id = ? AND symbol = ?
            """,
            (bot_id, symbol),
        )
        row = cursor.fetchone()
        delta = quantity if side == "BUY" else -quantity

        if not row:
            new_size = delta
            new_avg = price if abs(new_size) > _EPS else 0.0
            sl_pct = tp_pct = sl_price = tp_price = None
            if risk and abs(new_size) > _EPS:
                sl_pct, tp_pct, sl_price, tp_price = _risk_prices(
                    new_size, new_avg, **risk
                )
        else:
            current_size = float(row["size"])
            current_avg = float(row["avg_price"])
            new_size = current_size + delta
            if abs(new_size) <= _EPS:
                new_size = 0.0
                new_avg = 0.0
                sl_pct = tp_pct = sl_price = tp_price = None
            elif side == "BUY" and current_size >= 0:
                new_avg = ((current_size * current_avg) + quantity * price) / new_size
                sl_pct, tp_pct, sl_price, tp_price = (
                    _risk_prices(new_size, new_avg, **risk) if risk
                    else (
                        row["stop_loss_percent"],
                        row["take_profit_percent"],
                        row["stop_loss_price"],
                        row["take_profit_price"],
                    )
                )
            elif side == "SELL" and current_size < 0:
                new_avg = ((abs(current_size) * current_avg) + quantity * price) / abs(new_size)
                sl_pct, tp_pct, sl_price, tp_price = (
                    _risk_prices(new_size, new_avg, **risk) if risk
                    else (
                        row["stop_loss_percent"],
                        row["take_profit_percent"],
                        row["stop_loss_price"],
                        row["take_profit_price"],
                    )
                )
            else:
                new_avg = current_avg if new_size > 0 else (price if new_size < 0 else 0.0)
                sl_pct = tp_pct = sl_price = tp_price = None

        if abs(new_size) <= _EPS:
            cursor.execute(
                "DELETE FROM bot_positions WHERE bot_id = ? AND symbol = ?",
                (bot_id, symbol),
            )
        elif not row:
            cursor.execute(
                """
                INSERT INTO bot_positions (
                    bot_id, symbol, size, avg_price,
                    stop_loss_percent, take_profit_percent, stop_loss_price, take_profit_price
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (bot_id, symbol, new_size, new_avg, sl_pct, tp_pct, sl_price, tp_price),
            )
        else:
            cursor.execute(
                """
                UPDATE bot_positions
                SET size = ?, avg_price = ?,
                    stop_loss_percent = ?, take_profit_percent = ?,
                    stop_loss_price = ?, take_profit_price = ?
                WHERE bot_id = ? AND symbol = ?
                """,
                (new_size, new_avg, sl_pct, tp_pct, sl_price, tp_price, bot_id, symbol),
            )
        conn.commit()
    finally:
        conn.close()


def clear_symbol(symbol: str) -> None:
    if not symbol:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM bot_positions WHERE symbol = ?", (symbol,))
        conn.commit()
    finally:
        conn.close()


def clear_bot(bot_id: str) -> None:
    if not bot_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM bot_positions WHERE bot_id = ?", (bot_id,))
        conn.commit()
    finally:
        conn.close()
