"""Persistent bot trade history, snapshots, and aggregated stats."""

import json
import uuid

from app.config import BOT_SNAPSHOT_RETENTION
from app.database import get_connection

_snapshot_writes = 0


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


def _utc_day_bounds():
    from datetime import datetime, timedelta, timezone

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _stats_row(
    trade_count: int,
    exit_count: int,
    win_count: int,
    total_pnl: float,
    daily_pnl: float,
) -> dict:
    win_rate = round((win_count / exit_count) * 100, 1) if exit_count else 0.0
    return {
        "trade_count": int(trade_count or 0),
        "exit_count": int(exit_count or 0),
        "win_count": int(win_count or 0),
        "win_rate": win_rate,
        "total_pnl": round(float(total_pnl or 0), 2),
        "daily_pnl": round(float(daily_pnl or 0), 2),
    }


def _parse_insight_snapshot(raw) -> dict | None:
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


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
    insight_snapshot: dict | None = None,
):
    snapshot_json = json.dumps(insight_snapshot) if insight_snapshot else None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO bot_trades
        (bot_id, order_id, symbol, side, quantity, price, pnl, signal_id, signal_bar_time, is_exit, insight_snapshot)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bot_id, order_id, symbol, side, quantity, price, pnl, signal_id,
            signal_bar_time, 1 if is_exit else 0, snapshot_json,
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
               signal_bar_time, is_exit, timestamp, insight_snapshot
        FROM bot_trades WHERE bot_id = ?
        ORDER BY timestamp DESC LIMIT ?
        """,
        (bot_id, limit),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    for row in rows:
        row["insight_snapshot"] = _parse_insight_snapshot(row.get("insight_snapshot"))
    return rows


def get_daily_pnl(bot_id: str) -> float:
    day_start, day_end = _utc_day_bounds()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COALESCE(SUM(pnl), 0) FROM bot_trades
        WHERE bot_id = ? AND pnl IS NOT NULL AND timestamp >= ? AND timestamp < ?
        """,
        (bot_id, day_start, day_end),
    )
    val = float(cursor.fetchone()[0] or 0)
    conn.close()
    return val


def get_recent_consecutive_losses(bot_id: str) -> int:
    """Count current consecutive losing exits (most recent first)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT pnl FROM bot_trades
        WHERE bot_id = ? AND is_exit = 1 AND pnl IS NOT NULL
        ORDER BY timestamp DESC LIMIT 20
        """,
        (bot_id,),
    )
    streak = 0
    for row in cursor.fetchall():
        pnl = float(row[0] if not isinstance(row, dict) else row.get("pnl", 0))
        if pnl < 0:
            streak += 1
        else:
            break
    conn.close()
    return streak


def last_exit_timestamp(bot_id: str) -> str | None:
    """Return ISO timestamp of the most recent exit trade, or None."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT timestamp FROM bot_trades
        WHERE bot_id = ? AND is_exit = 1
        ORDER BY timestamp DESC LIMIT 1
        """,
        (bot_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return row[0] if not isinstance(row, dict) else row.get("timestamp")


def get_active_bots_for_symbol(symbol: str, *, exclude_bot_id: str = "") -> int:
    """Count running bots trading the given symbol (excludes the asking bot)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COUNT(*) FROM bots
        WHERE symbol = ? AND status = 'RUNNING' AND id != ?
        """,
        (symbol, exclude_bot_id),
    )
    row = cursor.fetchone()
    conn.close()
    return int(row[0] if row else 0)

def get_bot_stats(bot_id: str) -> dict:
    return get_all_bot_stats([bot_id]).get(
        bot_id,
        _stats_row(0, 0, 0, 0.0, 0.0),
    )


def get_all_bot_stats(bot_ids: list[str]) -> dict[str, dict]:
    """Batch stats for many bots in one query (avoids N+1 round-trips)."""
    if not bot_ids:
        return {}

    day_start, day_end = _utc_day_bounds()
    placeholders = ",".join("?" * len(bot_ids))
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT
            bot_id,
            COUNT(*) AS trade_count,
            SUM(CASE WHEN is_exit = 1 THEN 1 ELSE 0 END) AS exit_count,
            SUM(CASE WHEN is_exit = 1 AND pnl > 0 THEN 1 ELSE 0 END) AS win_count,
            COALESCE(SUM(CASE WHEN pnl IS NOT NULL THEN pnl ELSE 0 END), 0) AS total_pnl,
            COALESCE(SUM(
                CASE WHEN pnl IS NOT NULL AND timestamp >= ? AND timestamp < ?
                THEN pnl ELSE 0 END
            ), 0) AS daily_pnl
        FROM bot_trades
        WHERE bot_id IN ({placeholders})
        GROUP BY bot_id
        """,
        (day_start, day_end, *bot_ids),
    )
    out = {
        bot_id: _stats_row(0, 0, 0, 0.0, 0.0)
        for bot_id in bot_ids
    }
    for row in cursor.fetchall():
        bid = row["bot_id"] if isinstance(row, dict) else row[0]
        if isinstance(row, dict):
            out[bid] = _stats_row(
                row["trade_count"],
                row["exit_count"],
                row["win_count"],
                row["total_pnl"],
                row["daily_pnl"],
            )
        else:
            out[bid] = _stats_row(row[1], row[2], row[3], row[4], row[5])
    conn.close()
    return out


def record_snapshot(
    bot_id: str,
    equity: float,
    unrealized_pnl: float,
    realized_pnl: float,
    open_positions: int,
):
    global _snapshot_writes
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
    _snapshot_writes += 1
    if _snapshot_writes % 25 == 0:
        prune_bot_snapshots(BOT_SNAPSHOT_RETENTION)


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


def prune_bot_snapshots(keep: int = 2000):
    """Keep the most recent snapshot rows globally (append-only table)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        DELETE FROM bot_snapshots WHERE id NOT IN (
            SELECT id FROM bot_snapshots ORDER BY timestamp DESC LIMIT ?
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
    insight_snapshot: dict | None = None,
) -> str:
    """Queue a live order for broker confirmation before bot_trades write."""
    pending_id = str(uuid.uuid4())
    snapshot_json = json.dumps(insight_snapshot) if insight_snapshot else None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO bot_pending_fills
        (id, bot_id, order_id, symbol, side, quantity, signal_price, signal_id, is_exit, entry_price, insight_snapshot)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            snapshot_json,
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
                   signal_id, is_exit, entry_price, created_at, insight_snapshot
            FROM bot_pending_fills WHERE bot_id = ?
            ORDER BY created_at ASC
            """,
            (bot_id,),
        )
    else:
        cursor.execute(
            """
            SELECT id, bot_id, order_id, symbol, side, quantity, signal_price,
                   signal_id, is_exit, entry_price, created_at, insight_snapshot
            FROM bot_pending_fills
            ORDER BY created_at ASC
            """
        )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    for row in rows:
        row["insight_snapshot"] = _parse_insight_snapshot(row.get("insight_snapshot"))
    return rows


def delete_pending_fill(pending_id: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM bot_pending_fills WHERE id = ?", (pending_id,))
    conn.commit()
    conn.close()
