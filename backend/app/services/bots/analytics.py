"""Persistent bot trade history, snapshots, and aggregated stats."""

import uuid

from app.database import get_connection


def signal_bar_time_from_id(signal_id: str | None) -> int | None:
    """Extract closed-bar unix time embedded in bot signal_id (bot_id:bar_time:side)."""
    if not signal_id:
        return None
    parts = str(signal_id).split(":")
    if len(parts) < 3 or parts[1] == "sltp":
        return None
    try:
        val = int(float(parts[1]))
    except (TypeError, ValueError):
        return None
    return val if val > 1_000_000_000 else None


def record_trade(
    bot_id: str,
    order_id: str | None,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    *,
    pnl: float | None = None,
    signal_id: str | None = None,
    signal_bar_time: int | None = None,
    is_exit: bool = False,
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO bot_trades
        (bot_id, order_id, symbol, side, quantity, price, pnl, signal_id, signal_bar_time, is_exit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bot_id, order_id, symbol, side, quantity, price, pnl, signal_id,
            signal_bar_time, 1 if is_exit else 0,
        ),
    )
    conn.commit()
    conn.close()


def get_trades(bot_id: str, limit: int = 50) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, bot_id, order_id, symbol, side, quantity, price, pnl, signal_id,
               signal_bar_time, is_exit, timestamp
        FROM bot_trades WHERE bot_id = ?
        ORDER BY timestamp DESC LIMIT ?
        """,
        (bot_id, limit),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_daily_pnl(bot_id: str) -> float:
    from datetime import datetime, timedelta, timezone

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COALESCE(SUM(pnl), 0) FROM bot_trades
        WHERE bot_id = ? AND pnl IS NOT NULL AND timestamp >= ? AND timestamp < ?
        """,
        (bot_id, start.isoformat(), end.isoformat()),
    )
    val = float(cursor.fetchone()[0] or 0)
    conn.close()
    return val


def get_bot_stats(bot_id: str) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COUNT(*) FROM bot_trades WHERE bot_id = ? AND is_exit = 1
        """,
        (bot_id,),
    )
    exit_count = cursor.fetchone()[0] or 0

    cursor.execute(
        """
        SELECT COUNT(*) FROM bot_trades
        WHERE bot_id = ? AND is_exit = 1 AND pnl > 0
        """,
        (bot_id,),
    )
    win_count = cursor.fetchone()[0] or 0

    cursor.execute(
        """
        SELECT COALESCE(SUM(pnl), 0) FROM bot_trades
        WHERE bot_id = ? AND pnl IS NOT NULL
        """,
        (bot_id,),
    )
    total_pnl = float(cursor.fetchone()[0] or 0)

    cursor.execute(
        """
        SELECT COUNT(*) FROM bot_trades WHERE bot_id = ?
        """,
        (bot_id,),
    )
    trade_count = cursor.fetchone()[0] or 0
    conn.close()

    win_rate = round((win_count / exit_count) * 100, 1) if exit_count else 0.0
    return {
        "trade_count": trade_count,
        "exit_count": exit_count,
        "win_count": win_count,
        "win_rate": win_rate,
        "total_pnl": round(total_pnl, 2),
        "daily_pnl": round(get_daily_pnl(bot_id), 2),
    }


def record_snapshot(
    bot_id: str,
    equity: float,
    unrealized_pnl: float,
    realized_pnl: float,
    open_positions: int,
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO bot_snapshots (bot_id, equity, unrealized_pnl, realized_pnl, open_positions)
        VALUES (?, ?, ?, ?, ?)
        """,
        (bot_id, equity, unrealized_pnl, realized_pnl, open_positions),
    )
    conn.commit()
    conn.close()


def get_snapshots(bot_id: str, limit: int = 30) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT equity, unrealized_pnl, realized_pnl, open_positions, timestamp
        FROM bot_snapshots WHERE bot_id = ?
        ORDER BY timestamp DESC LIMIT ?
        """,
        (bot_id, limit),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def prune_bot_logs(keep: int = 500):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        DELETE FROM bot_logs WHERE id NOT IN (
            SELECT id FROM bot_logs ORDER BY timestamp DESC LIMIT ?
        )
        """,
        (keep,),
    )
    conn.commit()
    conn.close()


def clear_bot_analytics():
    """Wipe trade/snapshot/log analytics (bots table rows kept as STOPPED)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM bot_trades")
    cursor.execute("DELETE FROM bot_snapshots")
    cursor.execute("DELETE FROM bot_logs")
    cursor.execute("DELETE FROM bot_pending_fills")
    cursor.execute("DELETE FROM bot_positions")
    cursor.execute("DELETE FROM bot_signal_ledger")
    conn.commit()
    conn.close()


def record_pending_fill(
    bot_id: str,
    order_id: str | None,
    symbol: str,
    side: str,
    quantity: float,
    signal_price: float,
    *,
    signal_id: str | None = None,
    is_exit: bool = False,
    entry_price: float | None = None,
) -> str:
    """Queue a live order for broker confirmation before bot_trades write."""
    pending_id = str(uuid.uuid4())
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO bot_pending_fills
        (id, bot_id, order_id, symbol, side, quantity, signal_price, signal_id, is_exit, entry_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pending_id,
            bot_id,
            order_id,
            symbol,
            side,
            quantity,
            signal_price,
            signal_id,
            1 if is_exit else 0,
            entry_price,
        ),
    )
    conn.commit()
    conn.close()
    return pending_id


def list_pending_fills(*, bot_id: str | None = None) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    if bot_id:
        cursor.execute(
            """
            SELECT id, bot_id, order_id, symbol, side, quantity, signal_price,
                   signal_id, is_exit, entry_price, created_at
            FROM bot_pending_fills WHERE bot_id = ?
            ORDER BY created_at ASC
            """,
            (bot_id,),
        )
    else:
        cursor.execute(
            """
            SELECT id, bot_id, order_id, symbol, side, quantity, signal_price,
                   signal_id, is_exit, entry_price, created_at
            FROM bot_pending_fills
            ORDER BY created_at ASC
            """
        )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def delete_pending_fill(pending_id: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM bot_pending_fills WHERE id = ?", (pending_id,))
    conn.commit()
    conn.close()
