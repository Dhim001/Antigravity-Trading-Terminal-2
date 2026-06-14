"""Per-bot position slices (account positions remain aggregated in OMS)."""

from __future__ import annotations

from app.database import get_connection

_EPS = 1e-8


def get_bot_position(bot_id: str, symbol: str) -> dict:
    """Return bot-local size/avg for risk and snapshot math."""
    if not bot_id or not symbol:
        return {"size": 0.0, "avg_price": 0.0}
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT size, avg_price FROM bot_positions WHERE bot_id = ? AND symbol = ?",
            (bot_id, symbol),
        )
        row = cursor.fetchone()
        if not row:
            return {"size": 0.0, "avg_price": 0.0}
        return {"size": float(row["size"]), "avg_price": float(row["avg_price"])}
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


def get_symbol_owners(symbol: str) -> list[dict]:
    if not symbol:
        return []
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT bot_id, size, avg_price
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


def apply_fill(bot_id: str, symbol: str, side: str, quantity: float, price: float) -> None:
    """Update bot-local position after a fill attributed to bot_id."""
    if not bot_id or not symbol or quantity <= 0:
        return

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT size, avg_price FROM bot_positions WHERE bot_id = ? AND symbol = ?",
            (bot_id, symbol),
        )
        row = cursor.fetchone()
        delta = quantity if side == "BUY" else -quantity

        if not row:
            new_size = delta
            new_avg = price if abs(new_size) > _EPS else 0.0
        else:
            current_size = float(row["size"])
            current_avg = float(row["avg_price"])
            new_size = current_size + delta
            if abs(new_size) <= _EPS:
                new_size = 0.0
                new_avg = 0.0
            elif side == "BUY" and current_size >= 0:
                new_avg = ((current_size * current_avg) + quantity * price) / new_size
            elif side == "SELL" and current_size > 0:
                new_avg = current_avg if new_size > 0 else 0.0
            else:
                new_avg = price if abs(new_size) > _EPS else 0.0

        if abs(new_size) <= _EPS:
            cursor.execute(
                "DELETE FROM bot_positions WHERE bot_id = ? AND symbol = ?",
                (bot_id, symbol),
            )
        elif not row:
            cursor.execute(
                """
                INSERT INTO bot_positions (bot_id, symbol, size, avg_price)
                VALUES (?, ?, ?, ?)
                """,
                (bot_id, symbol, new_size, new_avg),
            )
        else:
            cursor.execute(
                """
                UPDATE bot_positions SET size = ?, avg_price = ?
                WHERE bot_id = ? AND symbol = ?
                """,
                (new_size, new_avg, bot_id, symbol),
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
