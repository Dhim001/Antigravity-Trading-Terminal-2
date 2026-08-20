"""Execution TCA — arrival-price benchmark + implementation shortfall capture.

EXECUTION_RISK_INTELLIGENCE_PLAN Phase 1. For every bot order we snapshot the
feed mark (and bid/ask when known) at order-decision time — the *arrival*
benchmark — and, once the order fills (immediately or via live-fill
reconciliation), decompose implementation shortfall (IS) in basis points into:

  IS = delay + spread + impact + opportunity

  delay_bps   drift between signal price and arrival mark (decision latency)
  spread_bps  half-spread at arrival, when bid/ask is known (cost of crossing)
  impact_bps  remaining exec cost vs arrival after spread (market impact /
              adverse selection; negative = price improvement)
  opp_bps     opportunity cost on the unfilled remainder (partial/no fill)

All measurements are signed such that positive = cost (bad for the trader):
BUY pays more / SELL receives less. Rows land in ``execution_quality_log``.

This module is read-only telemetry: every public entry point swallows its own
exceptions (logging at debug level) so a TCA failure can never break trading.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app import config
from app.database import get_connection

logger = logging.getLogger(__name__)

_BPS = 10_000.0


def _sign(side: str | None) -> float:
    return -1.0 if str(side or "").upper() == "SELL" else 1.0


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def capture_arrival(feed, symbol: str | None, fallback_price: float | None) -> dict:
    """Snapshot the arrival benchmark from the live feed.

    Returns ``{"arrival_price", "arrival_bid", "arrival_ask"}`` — all floats or
    None. Falls back to ``fallback_price`` (the signal price) when the feed has
    no mark for the symbol, so delay attribution degrades gracefully to 0.
    """
    snap: dict[str, float | None] = {
        "arrival_price": None,
        "arrival_bid": None,
        "arrival_ask": None,
    }
    try:
        info = None
        symbols_map = getattr(feed, "_symbols", None)
        if isinstance(symbols_map, dict) and symbol:
            info = symbols_map.get(str(symbol)) or symbols_map.get(str(symbol).upper())
        if isinstance(info, dict):
            snap["arrival_price"] = _f(info.get("price"))
            snap["arrival_bid"] = _f(info.get("bid"))
            snap["arrival_ask"] = _f(info.get("ask"))
    except Exception:
        logger.debug("capture_arrival failed for %s", symbol, exc_info=True)
    if snap["arrival_price"] is None:
        snap["arrival_price"] = _f(fallback_price)
    return snap


def compute_is(
    *,
    side: str,
    decision_price: float | None,
    arrival_price: float | None,
    arrival_bid: float | None = None,
    arrival_ask: float | None = None,
    avg_fill_price: float | None,
    requested_qty: float | None,
    filled_qty: float | None,
    end_mark: float | None = None,
) -> dict:
    """Pure IS decomposition in bps. All fields None when not computable.

    Sign convention: positive = cost. ``end_mark`` prices the unfilled
    remainder for opportunity cost (defaults to arrival ⇒ opp ≈ 0).
    """
    sign = _sign(side)
    decision = _f(decision_price)
    arrival = _f(arrival_price)
    fill = _f(avg_fill_price)
    req = _f(requested_qty) or 0.0
    filled = max(0.0, _f(filled_qty) or 0.0)

    out: dict[str, float | None] = {
        "is_bps": None,
        "delay_bps": None,
        "spread_bps": None,
        "impact_bps": None,
        "opp_bps": None,
        "unfilled_qty": None,
    }

    if arrival is not None and arrival > 0 and decision is not None and decision > 0:
        out["delay_bps"] = sign * (arrival - decision) / decision * _BPS

    unfilled = max(0.0, req - filled)
    out["unfilled_qty"] = unfilled if req > 0 else None

    if arrival is not None and arrival > 0 and fill is not None and filled > 0:
        exec_bps = sign * (fill - arrival) / arrival * _BPS
        # Half-spread at arrival, when the quote is known.
        bid = _f(arrival_bid)
        ask = _f(arrival_ask)
        if bid is not None and ask is not None and ask >= bid and bid > 0:
            mid = (bid + ask) / 2.0
            if mid > 0:
                out["spread_bps"] = (ask - bid) / 2.0 / mid * _BPS
                out["impact_bps"] = exec_bps - out["spread_bps"]
        if out["impact_bps"] is None:
            out["impact_bps"] = exec_bps
        if decision is not None and decision > 0:
            out["is_bps"] = sign * (fill - decision) / decision * _BPS

    if unfilled > 0 and req > 0 and arrival is not None and arrival > 0:
        mark = _f(end_mark)
        if mark is not None and mark > 0:
            out["opp_bps"] = (
                sign * (mark - arrival) / arrival * _BPS * (unfilled / req)
            )
    return out


def record_execution(
    *,
    bot_id: str,
    symbol: str | None,
    strategy: str | None,
    side: str,
    is_exit: bool,
    exec_algo: str | None,
    order_id: str | None,
    signal_id: str | None,
    decision_price: float | None,
    arrival: dict | None = None,
    arrival_price: float | None = None,
    arrival_bid: float | None = None,
    arrival_ask: float | None = None,
    requested_qty: float | None,
    filled_qty: float | None,
    avg_fill_price: float | None,
    fees: float | None = None,
    end_mark: float | None = None,
) -> None:
    """Compute the IS decomposition and persist one execution-quality row.

    Never raises — telemetry must not affect the order path.
    """
    if not getattr(config, "EXEC_QUALITY_LOG_ENABLED", True):
        return
    try:
        arr = dict(arrival or {})
        a_price = _f(arr.get("arrival_price"))
        a_bid = _f(arr.get("arrival_bid"))
        a_ask = _f(arr.get("arrival_ask"))
        if a_price is None:
            a_price = _f(arrival_price)
        if a_bid is None:
            a_bid = _f(arrival_bid)
        if a_ask is None:
            a_ask = _f(arrival_ask)

        decomp = compute_is(
            side=side,
            decision_price=decision_price,
            arrival_price=a_price,
            arrival_bid=a_bid,
            arrival_ask=a_ask,
            avg_fill_price=avg_fill_price,
            requested_qty=requested_qty,
            filled_qty=filled_qty,
            end_mark=end_mark,
        )
        if decomp["is_bps"] is None and decomp["opp_bps"] is None:
            # Nothing measurable (no fill and no priced remainder) — skip.
            return

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO execution_quality_log
            (bot_id, symbol, strategy, side, is_exit, exec_algo, order_id, signal_id,
             decision_price, arrival_price, arrival_bid, arrival_ask,
             requested_qty, filled_qty, avg_fill_price,
             is_bps, delay_bps, spread_bps, impact_bps, opp_bps, fees)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bot_id,
                symbol,
                strategy,
                side,
                1 if is_exit else 0,
                exec_algo,
                order_id,
                signal_id,
                _f(decision_price),
                a_price,
                a_bid,
                a_ask,
                _f(requested_qty),
                _f(filled_qty),
                _f(avg_fill_price),
                decomp["is_bps"],
                decomp["delay_bps"],
                decomp["spread_bps"],
                decomp["impact_bps"],
                decomp["opp_bps"],
                _f(fees),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.debug("record_execution failed for bot %s", bot_id, exc_info=True)


def _utcnow_naive() -> datetime:
    """Naive UTC now matching the TIMESTAMP DEFAULT CURRENT_TIMESTAMP format."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def prune_execution_quality_log(retention_days: int, *, max_rows: int | None = None) -> int:
    """Retention for execution_quality_log: drop rows older than the window,
    then hard-cap total rows (oldest first). Returns rows deleted."""
    deleted = 0
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if retention_days and retention_days > 0:
            cutoff = (_utcnow_naive() - timedelta(days=retention_days)).isoformat(sep=" ")
            cursor.execute(
                "DELETE FROM execution_quality_log WHERE created_at < ?", (cutoff,)
            )
            deleted += cursor.rowcount or 0
        if max_rows and max_rows > 0:
            cursor.execute("SELECT COUNT(*) FROM execution_quality_log")
            total = int(cursor.fetchone()[0] or 0)
            overflow = total - max_rows
            if overflow > 0:
                cursor.execute(
                    """
                    DELETE FROM execution_quality_log WHERE id IN (
                        SELECT id FROM execution_quality_log
                        ORDER BY created_at ASC, id ASC LIMIT ?
                    )
                    """,
                    (overflow,),
                )
                deleted += cursor.rowcount or 0
        conn.commit()
        conn.close()
    except Exception:
        logger.debug("prune_execution_quality_log failed", exc_info=True)
    return deleted


def measured_symbol_impact(symbol: str | None) -> dict | None:
    """Measured avg impact/delay for one symbol — feeds Phase 3 adaptive algo
    choice. Returns None when no filled rows exist."""
    if not symbol:
        return None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS n,
                   AVG(impact_bps) AS avg_impact_bps,
                   AVG(spread_bps) AS avg_spread_bps,
                   AVG(delay_bps) AS avg_delay_bps
            FROM execution_quality_log
            WHERE symbol = ? AND filled_qty > 0 AND avg_fill_price IS NOT NULL
            """,
            (symbol,),
        )
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return None
        out = dict(row)
        if not out.get("n"):
            return None
        return out
    except Exception:
        logger.debug("measured_symbol_impact failed for %s", symbol, exc_info=True)
        return None


def mean_is_bps_for_symbol(symbol: str, *, lookback_days: int = 30) -> float | None:
    """Mean implementation shortfall (bps) for a symbol over the trailing window.

    Returns None when there are no measured fills. Never raises.
    """
    if not symbol:
        return None
    conn = None
    try:
        cutoff = (_utcnow_naive() - timedelta(days=max(1, lookback_days))).isoformat(sep=" ")
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT AVG(is_bps) AS avg_is_bps
            FROM execution_quality_log
            WHERE symbol = ? AND is_bps IS NOT NULL AND created_at >= ?
            """,
            (str(symbol).upper(), cutoff),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        val = row.get("avg_is_bps") if isinstance(row, dict) else row[0]
        return float(val) if val is not None else None
    except Exception:
        logger.debug("mean_is_bps_for_symbol failed for %s", symbol, exc_info=True)
        return None
    finally:
        # A failed query must not leak the pooled connection.
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _quality_filters(
    *, bot_id: str | None, symbol: str | None, strategy: str | None, hours: int | None
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if bot_id:
        clauses.append("bot_id = ?")
        params.append(bot_id)
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol)
    if strategy:
        clauses.append("strategy = ?")
        params.append(strategy)
    if hours and hours > 0:
        cutoff = (_utcnow_naive() - timedelta(hours=hours)).isoformat(sep=" ")
        clauses.append("created_at >= ?")
        params.append(cutoff)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def execution_quality_dashboard(
    *,
    bot_id: str | None = None,
    symbol: str | None = None,
    strategy: str | None = None,
    hours: int | None = None,
    worst_limit: int = 10,
) -> dict:
    """Full Phase 2 aggregate bundle: KPIs, algo/symbol/strategy breakdowns,
    daily IS trend, and the worst fills list."""
    where, params = _quality_filters(
        bot_id=bot_id, symbol=symbol, strategy=strategy, hours=hours
    )
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        f"""
        SELECT COUNT(*) AS n,
               AVG(is_bps) AS avg_is_bps,
               AVG(delay_bps) AS avg_delay_bps,
               AVG(spread_bps) AS avg_spread_bps,
               AVG(impact_bps) AS avg_impact_bps,
               AVG(opp_bps) AS avg_opp_bps,
               SUM(filled_qty) AS total_filled
        FROM execution_quality_log {where}
        """,
        tuple(params),
    )
    kpis = dict(cursor.fetchone())

    cursor.execute(
        f"""
        SELECT exec_algo, side, COUNT(*) AS n,
               AVG(is_bps) AS avg_is_bps, AVG(delay_bps) AS avg_delay_bps,
               AVG(spread_bps) AS avg_spread_bps, AVG(impact_bps) AS avg_impact_bps,
               AVG(opp_bps) AS avg_opp_bps, SUM(filled_qty) AS total_filled
        FROM execution_quality_log {where}
        GROUP BY exec_algo, side
        ORDER BY n DESC
        """,
        tuple(params),
    )
    by_algo = [dict(r) for r in cursor.fetchall()]

    cursor.execute(
        f"""
        SELECT symbol, COUNT(*) AS n, AVG(is_bps) AS avg_is_bps,
               AVG(impact_bps) AS avg_impact_bps, AVG(opp_bps) AS avg_opp_bps
        FROM execution_quality_log {where}
        GROUP BY symbol
        ORDER BY n DESC
        """,
        tuple(params),
    )
    by_symbol = [dict(r) for r in cursor.fetchall()]

    cursor.execute(
        f"""
        SELECT strategy, COUNT(*) AS n, AVG(is_bps) AS avg_is_bps,
               AVG(impact_bps) AS avg_impact_bps, AVG(opp_bps) AS avg_opp_bps
        FROM execution_quality_log {where}
        GROUP BY strategy
        ORDER BY n DESC
        """,
        tuple(params),
    )
    by_strategy = [dict(r) for r in cursor.fetchall()]

    # Daily IS trend — substr() keeps this portable across sqlite/Postgres.
    cursor.execute(
        f"""
        SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n,
               AVG(is_bps) AS avg_is_bps
        FROM execution_quality_log {where}
        GROUP BY day
        ORDER BY day ASC
        """,
        tuple(params),
    )
    trend = [dict(r) for r in cursor.fetchall()]

    cursor.execute(
        f"""
        SELECT bot_id, symbol, strategy, side, exec_algo, order_id,
               decision_price, arrival_price, avg_fill_price,
               is_bps, delay_bps, impact_bps, opp_bps, created_at
        FROM execution_quality_log {where}
        ORDER BY (is_bps IS NULL), is_bps DESC
        LIMIT ?
        """,
        tuple(params) + (max(1, int(worst_limit)),),
    )
    worst_fills = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return {
        "kpis": kpis,
        "by_algo": by_algo,
        "by_symbol": by_symbol,
        "by_strategy": by_strategy,
        "trend": trend,
        "worst_fills": worst_fills,
    }


def execution_quality_summary(
    *, bot_id: str | None = None, hours: int | None = None
) -> list[dict]:
    """Aggregate IS stats grouped by (exec_algo, side) — feeds Phase 2 APIs."""
    clauses: list[str] = []
    params: list[Any] = []
    if bot_id:
        clauses.append("bot_id = ?")
        params.append(bot_id)
    if hours and hours > 0:
        cutoff = (_utcnow_naive() - timedelta(hours=hours)).isoformat(sep=" ")
        clauses.append("created_at >= ?")
        params.append(cutoff)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT exec_algo, side, COUNT(*) AS n,
               AVG(is_bps) AS avg_is_bps,
               AVG(delay_bps) AS avg_delay_bps,
               AVG(spread_bps) AS avg_spread_bps,
               AVG(impact_bps) AS avg_impact_bps,
               AVG(opp_bps) AS avg_opp_bps,
               SUM(filled_qty) AS total_filled
        FROM execution_quality_log {where}
        GROUP BY exec_algo, side
        ORDER BY n DESC
        """,
        tuple(params),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows
