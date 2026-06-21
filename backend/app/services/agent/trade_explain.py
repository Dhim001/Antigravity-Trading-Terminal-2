"""Retrieve and explain bot trade context (insight + logs) — lightweight RAG."""

from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_connection
from app.services.market.timeframes import normalize_timeframe


def _parse_payload(raw: Any) -> dict | None:
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def _find_insight(
    symbol: str,
    bar_time: int | None,
    timeframe: str,
    *,
    chart_analyst=None,
) -> dict | None:
    if bar_time is None:
        return None
    tf = normalize_timeframe(timeframe)
    sym = symbol.upper()

    if chart_analyst is not None:
        rows = chart_analyst.list_insights(sym, limit=30, timeframe=tf)
        for row in rows:
            if row.get("bar_time") == bar_time:
                return row
        for row in rows:
            if normalize_timeframe(row.get("timeframe", "1m")) == tf:
                return row

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT payload FROM agent_insights
        WHERE symbol = ? AND bar_time = ?
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (sym, bar_time),
    )
    rows = cursor.fetchall()
    conn.close()
    for row in rows:
        payload = _parse_payload(row[0] if not isinstance(row, dict) else row.get("payload"))
        if not payload:
            continue
        if normalize_timeframe(payload.get("timeframe", "1m")) == tf:
            return payload
    return None


def _fetch_trade(bot_id: str, trade_id: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, bot_id, symbol, side, price, quantity, timestamp, is_exit,
               signal_id, signal_bar_time, order_id, insight_snapshot
        FROM bot_trades
        WHERE bot_id = ? AND id = ?
        """,
        (bot_id, trade_id),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    if isinstance(row, dict):
        out = dict(row)
        out["insight_snapshot"] = _parse_payload(out.get("insight_snapshot"))
        return out
    return {
        "id": row[0],
        "bot_id": row[1],
        "symbol": row[2],
        "side": row[3],
        "price": row[4],
        "quantity": row[5],
        "timestamp": row[6],
        "is_exit": row[7],
        "signal_id": row[8],
        "signal_bar_time": row[9],
        "order_id": row[10],
        "insight_snapshot": _parse_payload(row[11]) if len(row) > 11 else None,
    }


def _fetch_bot(bot_id: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, strategy, symbol, timeframe, config FROM bots WHERE id = ?",
        (bot_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    if isinstance(row, dict):
        cfg = row.get("config")
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
        return {**row, "config": cfg or {}}
    cfg = json.loads(row[4]) if row[4] else {}
    return {
        "id": row[0],
        "strategy": row[1],
        "symbol": row[2],
        "timeframe": row[3],
        "config": cfg,
    }


def _fetch_recent_logs(bot_id: str, limit: int = 12) -> list[str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT message FROM bot_logs
        WHERE bot_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (bot_id, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return [r[0] if not isinstance(r, dict) else r.get("message", "") for r in rows if r]


def _fetch_related_insights(symbol: str, timeframe: str, *, limit: int = 5) -> list[dict]:
    """Recent analyst insights for symbol (RAG context)."""
    sym = symbol.upper()
    tf = normalize_timeframe(timeframe)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT payload FROM agent_insights
        WHERE symbol = ?
        ORDER BY bar_time DESC
        LIMIT ?
        """,
        (sym, limit * 3),
    )
    rows = cursor.fetchall()
    conn.close()
    out: list[dict] = []
    for row in rows:
        payload = _parse_payload(row[0] if not isinstance(row, dict) else row.get("payload"))
        if not payload:
            continue
        if normalize_timeframe(payload.get("timeframe", "1m")) != tf:
            continue
        out.append({
            "bar_time": payload.get("bar_time"),
            "signal": payload.get("signal"),
            "confidence": payload.get("confidence"),
            "score": payload.get("score"),
            "reasons": (payload.get("reasons") or [])[:3],
        })
        if len(out) >= limit:
            break
    return out


def _fetch_related_trades(bot_id: str, symbol: str, *, limit: int = 5) -> list[dict]:
    """Recent fills on same bot/symbol for context."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT side, price, quantity, timestamp, is_exit, signal_bar_time
        FROM bot_trades
        WHERE bot_id = ? AND symbol = ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (bot_id, symbol.upper(), limit),
    )
    rows = cursor.fetchall()
    conn.close()
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append(dict(row))
        else:
            out.append({
                "side": row[0],
                "price": row[1],
                "quantity": row[2],
                "timestamp": row[3],
                "is_exit": row[4],
                "signal_bar_time": row[5],
            })
    return out


def _template_summary(trade: dict, insight: dict | None, bot: dict) -> str:
    side = trade.get("side", "?")
    sym = trade.get("symbol", "?")
    if trade.get("is_exit"):
        return f"Exit {side} on {sym} — position close."
    if not insight:
        return f"Entry {side} on {sym} — no matching analyst insight for bar {trade.get('signal_bar_time')}."
    signal = insight.get("signal", "NONE")
    conf = insight.get("confidence")
    conf_pct = f"{round(conf * 100)}%" if conf is not None else "—"
    reasons = insight.get("reasons") or []
    top = reasons[0] if reasons else "rule engine signal"
    tf = insight.get("timeframe") or bot.get("timeframe") or "1m"
    return (
        f"Entry {side} on {sym} ({tf}): analyst {signal} at {conf_pct} confidence — {top}."
    )


async def explain_trade(
    bot_id: str,
    trade_id: str,
    *,
    chart_analyst=None,
    use_llm: bool = False,
    llm_model: str | None = None,
) -> dict[str, Any]:
    trade = _fetch_trade(bot_id, trade_id)
    if not trade:
        raise ValueError("Trade not found")

    bot = _fetch_bot(bot_id) or {}
    timeframe = (
        bot.get("timeframe")
        or (bot.get("config") or {}).get("timeframe")
        or "1m"
    )
    symbol = trade.get("symbol") or bot.get("symbol") or ""

    insight = None
    if not trade.get("is_exit"):
        insight = _parse_payload(trade.get("insight_snapshot"))
        if not insight:
            insight = _find_insight(
                symbol,
                trade.get("signal_bar_time"),
                timeframe,
                chart_analyst=chart_analyst,
            )

    logs = _fetch_recent_logs(bot_id)
    related_insights = _fetch_related_insights(symbol, timeframe, limit=5)
    related_trades = _fetch_related_trades(bot_id, symbol, limit=5)
    summary = _template_summary(trade, insight, bot)

    narrative = None
    llm_provider = None
    if use_llm and insight:
        try:
            from app.services.agent.llm.router import summarize_insight

            bundle = {
                **insight,
                "trade_context": {
                    "side": trade.get("side"),
                    "price": trade.get("price"),
                    "quantity": trade.get("quantity"),
                    "signal_bar_time": trade.get("signal_bar_time"),
                },
                "recent_logs": logs[:6],
                "related_insights": related_insights,
                "related_trades": related_trades,
            }
            narrative, _model, llm_provider = await summarize_insight(bundle, model=llm_model)
        except Exception:
            narrative = None

    return {
        "trade_id": trade_id,
        "bot_id": bot_id,
        "summary": summary,
        "trade": trade,
        "bot": {
            "strategy": bot.get("strategy"),
            "timeframe": timeframe,
            "symbol": symbol,
        },
        "insight": insight,
        "recent_logs": logs,
        "related_insights": related_insights,
        "related_trades": related_trades,
        "narrative": narrative,
        "llm_provider": llm_provider,
        "sources": [
            s for s, ok in [
                ("bot_trades", bool(trade)),
                ("agent_insights", bool(insight)),
                ("bot_logs", bool(logs)),
                ("related_insights", bool(related_insights)),
                ("related_trades", bool(related_trades)),
            ] if ok
        ],
    }
