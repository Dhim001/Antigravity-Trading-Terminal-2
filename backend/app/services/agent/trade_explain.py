"""Retrieve and explain bot trade context (insight + logs) — lightweight RAG."""

from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_connection
from app.services.agent.regime_routing import current_atr_regime
from app.services.market.timeframes import normalize_timeframe, timeframe_to_secs


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
        nearest = _nearest_insight_row(rows, bar_time, tf)
        if nearest:
            return nearest

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

    # Nearest-bar fallback within one bar period (handles resample alignment drift).
    try:
        period = timeframe_to_secs(tf)
    except ValueError:
        period = 60
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT payload, bar_time FROM agent_insights
        WHERE symbol = ? AND bar_time BETWEEN ? AND ?
        ORDER BY ABS(bar_time - ?) ASC
        LIMIT 10
        """,
        (sym, bar_time - period, bar_time + period, bar_time),
    )
    near_rows = cursor.fetchall()
    conn.close()
    for row in near_rows:
        payload = _parse_payload(row[0] if not isinstance(row, dict) else row.get("payload"))
        if not payload:
            continue
        if normalize_timeframe(payload.get("timeframe", "1m")) == tf:
            return payload

    if chart_analyst is not None:
        rows = chart_analyst.list_insights(sym, limit=30, timeframe=tf)
        return _nearest_insight_row(rows, bar_time, tf)
    return None


def _nearest_insight_row(rows: list[dict], bar_time: int, tf: str) -> dict | None:
    try:
        period = timeframe_to_secs(tf)
    except ValueError:
        period = 60
    best: dict | None = None
    best_delta = period + 1
    for row in rows or []:
        if normalize_timeframe(row.get("timeframe", "1m")) != tf:
            continue
        bt = row.get("bar_time")
        if bt is None:
            continue
        delta = abs(int(bt) - int(bar_time))
        if delta <= period and delta < best_delta:
            best = row
            best_delta = delta
    return best


def _fetch_entry_for_exit(bot_id: str, exit_trade: dict) -> dict | None:
    """Find the opening fill that this exit likely closes."""
    sym = (exit_trade.get("symbol") or "").upper()
    exit_ts = exit_trade.get("timestamp")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT side, price, quantity, timestamp, signal_bar_time, insight_snapshot, is_exit
        FROM bot_trades
        WHERE bot_id = ? AND symbol = ? AND is_exit = 0
        ORDER BY timestamp DESC
        LIMIT 20
        """,
        (bot_id, sym),
    )
    rows = cursor.fetchall()
    conn.close()
    candidates: list[dict] = []
    for row in rows:
        if isinstance(row, dict):
            candidates.append(dict(row))
        else:
            candidates.append({
                "side": row[0],
                "price": row[1],
                "quantity": row[2],
                "timestamp": row[3],
                "signal_bar_time": row[4],
                "insight_snapshot": row[5],
                "is_exit": row[6],
            })
    if not candidates:
        return None
    if exit_ts:
        for entry in candidates:
            if entry.get("timestamp") and entry["timestamp"] <= exit_ts:
                return entry
    return candidates[0]


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


def _fetch_trade_relevant_logs(trade: dict, bot_id: str, *, limit: int = 12) -> list[str]:
    """Rank recent bot logs by relevance to this fill (lightweight RAG)."""
    sym = (trade.get("symbol") or "").upper()
    side = (trade.get("side") or "").upper()
    bar_time = trade.get("signal_bar_time")
    raw = _fetch_recent_logs(bot_id, limit=40)
    scored: list[tuple[int, str]] = []
    for msg in raw:
        text = str(msg or "")
        if not text:
            continue
        upper = text.upper()
        score = 0
        if sym and sym in upper:
            score += 2
        if side and side in upper:
            score += 1
        if bar_time is not None and str(bar_time) in text:
            score += 3
        lower = text.lower()
        if any(k in lower for k in ("signal", "entry", "insight", "chart", "analyst", "buy", "sell")):
            score += 1
        scored.append((score, text))
    scored.sort(key=lambda item: -item[0])
    out: list[str] = []
    seen: set[str] = set()
    for _, msg in scored:
        if msg in seen:
            continue
        seen.add(msg)
        out.append(msg)
        if len(out) >= limit:
            return out
    for msg in raw:
        text = str(msg or "")
        if text and text not in seen:
            out.append(text)
            if len(out) >= limit:
                break
    return out


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
            "sub_reports": {
                name: {
                    k: report.get(k)
                    for k in ("score", "atr_regime", "suggested_size_factor")
                    if isinstance(report, dict) and report.get(k) is not None
                }
                for name, report in (payload.get("sub_reports") or {}).items()
                if isinstance(report, dict)
            },
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


def _fetch_vision_report(symbol: str, bar_time: int | None) -> dict | None:
    """Lookup persisted vision (SQLite) near trade signal bar."""
    from app.services.agent.vision_store import lookup_vision_near_bar

    return lookup_vision_near_bar(symbol, bar_time)


def _fetch_regime_context(insight: dict | None) -> dict | None:
    """Volatility regime and size factor from analyst risk sub-report."""
    if not insight:
        return None
    sub = insight.get("sub_reports") or {}
    risk = sub.get("risk") or {}
    regime = current_atr_regime(insight)
    out: dict[str, Any] = {"atr_regime": regime}
    size_factor = risk.get("suggested_size_factor")
    if size_factor is not None:
        out["suggested_size_factor"] = size_factor
    notes = {
        "elevated": "Elevated volatility — entries typically require higher confidence.",
        "compressed": "Compressed volatility — range-bound conditions; breakouts may be muted.",
        "normal": "Normal volatility regime.",
    }
    out["note"] = notes.get(regime, notes["normal"])
    return out


def _peer_symbols(symbol: str, group: str) -> list[str]:
    """Other symbols in the same correlation group (static or dynamic)."""
    from app.config import CORRELATION_GROUPS
    from app.services.bots.correlation import get_correlation_store

    sym = str(symbol or "").upper()
    peers: list[str] = []
    snap = get_correlation_store().get_snapshot()
    for members in (snap.groups or {}).values():
        if sym in members:
            peers = [s for s in members if s != sym]
            break
    if not peers:
        for gname, members in CORRELATION_GROUPS.items():
            if sym in members or group == gname:
                peers = [s for s in members if s != sym]
                if sym in members:
                    break
    return peers[:5]


def _return_pct_near_bar(symbol: str, bar_time: int, window_secs: int = 3600) -> float | None:
    """1h return % ending at bar_time from archived 1m bars (when available)."""
    try:
        from app.services.archive.query import query_1m

        bars = query_1m(symbol, int(bar_time) - window_secs, int(bar_time))
        if len(bars) < 2:
            return None
        first = float(bars[0]["close"])
        last = float(bars[-1]["close"])
        if first <= 0:
            return None
        return round((last / first - 1.0) * 100.0, 2)
    except Exception:
        return None


def _fetch_correlated_context(symbol: str, bar_time: int | None) -> dict | None:
    """Correlation group label and peer return behavior around the trade bar."""
    from app.services.bots.correlation import resolve_correlation_group

    sym = str(symbol or "").upper()
    if not sym:
        return None
    group = resolve_correlation_group(sym)
    peers = _peer_symbols(sym, group)
    out: dict[str, Any] = {
        "group": group,
        "symbol": sym,
        "peers": peers,
    }
    if bar_time is not None:
        sym_ret = _return_pct_near_bar(sym, int(bar_time))
        if sym_ret is not None:
            out["symbol_return_pct_1h"] = sym_ret
        peer_rows: list[dict[str, Any]] = []
        returns: list[float] = []
        for peer in peers:
            ret = _return_pct_near_bar(peer, int(bar_time))
            row: dict[str, Any] = {"symbol": peer}
            if ret is not None:
                row["return_pct_1h"] = ret
                returns.append(ret)
            peer_rows.append(row)
        if peer_rows:
            out["peer_returns"] = peer_rows
        if returns:
            returns.sort()
            mid = len(returns) // 2
            median = returns[mid] if len(returns) % 2 else (returns[mid - 1] + returns[mid]) / 2
            out["group_median_return_pct_1h"] = round(median, 2)
    return out


def _fetch_anomaly_context(insight: dict | None) -> dict | None:
    if not insight:
        return None
    anomaly = (insight.get("sub_reports") or {}).get("anomaly")
    if not isinstance(anomaly, dict) or not anomaly.get("is_anomaly"):
        return None
    return {
        k: anomaly[k]
        for k in ("is_anomaly", "kinds", "volume_z", "return_z", "gap_pct", "summary")
        if anomaly.get(k) is not None
    }


def _fetch_events_context(
    symbol: str,
    *,
    timestamp: Any = None,
    bar_time: int | None = None,
) -> dict | None:
    from app.services.altdata.store import get_events_near_trade

    events = get_events_near_trade(symbol, timestamp=timestamp, bar_time=bar_time, window_hours=24.0)
    if not events.get("corporate") and not events.get("economic"):
        return events if events.get("trade_time") else None
    return events


def _template_summary(
    trade: dict,
    insight: dict | None,
    bot: dict,
    vision: dict | None = None,
    *,
    regime: dict | None = None,
    correlated: dict | None = None,
    events: dict | None = None,
) -> str:
    side = trade.get("side", "?")
    sym = trade.get("symbol", "?")
    tf = (insight or {}).get("timeframe") or bot.get("timeframe") or "1m"
    regime_bit = ""
    if regime and regime.get("atr_regime") and regime["atr_regime"] != "normal":
        regime_bit = f" Vol regime: {regime['atr_regime']}."
    corr_bit = ""
    if correlated and correlated.get("group_median_return_pct_1h") is not None:
        med = correlated["group_median_return_pct_1h"]
        corr_bit = f" {correlated.get('group', 'Group')} peers median 1h: {med:+.1f}%."
    event_bit = ""
    corp = (events or {}).get("corporate") or []
    macro = (events or {}).get("economic") or []
    if corp:
        title = corp[0].get("title") or corp[0].get("event_type") or "corporate event"
        event_bit = f" Near event: {title[:60]}."
    elif macro:
        title = macro[0].get("title") or macro[0].get("event_type") or "macro event"
        event_bit = f" Near macro: {title[:60]}."
    if trade.get("is_exit"):
        if insight:
            signal = insight.get("signal", "NONE")
            conf = insight.get("confidence")
            conf_pct = f"{round(conf * 100)}%" if conf is not None else "—"
            reasons = insight.get("reasons") or []
            top = reasons[0] if reasons else "prior entry context"
            return (
                f"Exit {side} on {sym} ({tf}) — closed position opened on "
                f"{signal} setup ({conf_pct} conf): {top}.{regime_bit}{corr_bit}{event_bit}"
            )
        return f"Exit {side} on {sym} — position close.{regime_bit}{corr_bit}{event_bit}"
    if not insight:
        return f"Entry {side} on {sym} — no matching analyst insight for bar {trade.get('signal_bar_time')}."
    signal = insight.get("signal", "NONE")
    conf = insight.get("confidence")
    conf_pct = f"{round(conf * 100)}%" if conf is not None else "—"
    reasons = insight.get("reasons") or []
    top = reasons[0] if reasons else "rule engine signal"
    vision_bit = ""
    if vision and vision.get("structure"):
        vision_bit = f" Structure ({vision.get('timeframe', '4h')}): {str(vision['structure'])[:120]}."
    return (
        f"Entry {side} on {sym} ({tf}): analyst {signal} at {conf_pct} confidence — {top}."
        f"{vision_bit}{regime_bit}{corr_bit}{event_bit}"
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
    if trade.get("is_exit"):
        entry = _fetch_entry_for_exit(bot_id, trade)
        if entry:
            insight = _parse_payload(entry.get("insight_snapshot"))
            if not insight and entry.get("signal_bar_time") is not None:
                insight = _find_insight(
                    symbol,
                    entry.get("signal_bar_time"),
                    timeframe,
                    chart_analyst=chart_analyst,
                )
    elif not trade.get("is_exit"):
        insight = _parse_payload(trade.get("insight_snapshot"))
        if not insight:
            insight = _find_insight(
                symbol,
                trade.get("signal_bar_time"),
                timeframe,
                chart_analyst=chart_analyst,
            )

    logs = _fetch_trade_relevant_logs(trade, bot_id)
    related_insights = _fetch_related_insights(symbol, timeframe, limit=5)
    related_trades = _fetch_related_trades(bot_id, symbol, limit=5)
    bar_for_vision = trade.get("signal_bar_time")
    if trade.get("is_exit") and not bar_for_vision:
        entry = _fetch_entry_for_exit(bot_id, trade)
        bar_for_vision = entry.get("signal_bar_time") if entry else None
    vision_report = _fetch_vision_report(symbol, bar_for_vision)
    if not vision_report and insight:
        from app.services.agent.vision_store import search_vision_semantic, slim_vision_payload

        reasons = " ".join(insight.get("reasons") or [])
        query = f"{symbol} {insight.get('signal', '')} {reasons}".strip()
        if query:
            related = search_vision_semantic(symbol, query, limit=1)
            if related:
                vision_report = slim_vision_payload(related[0])

    regime_context = _fetch_regime_context(insight)
    anomaly_context = _fetch_anomaly_context(insight)
    correlated_context = _fetch_correlated_context(symbol, bar_for_vision or trade.get("signal_bar_time"))
    events_context = _fetch_events_context(
        symbol,
        timestamp=trade.get("timestamp"),
        bar_time=bar_for_vision or trade.get("signal_bar_time"),
    )

    summary = _template_summary(
        trade, insight, bot, vision_report,
        regime=regime_context,
        correlated=correlated_context,
        events=events_context,
    )

    narrative = None
    llm_provider = None
    if use_llm and (insight or logs or related_insights or regime_context or correlated_context):
        try:
            from app.services.agent.llm.router import summarize_trade_explain

            bundle = {
                "insight": insight or {},
                "trade_context": {
                    "side": trade.get("side"),
                    "price": trade.get("price"),
                    "quantity": trade.get("quantity"),
                    "signal_bar_time": trade.get("signal_bar_time"),
                    "is_exit": bool(trade.get("is_exit")),
                },
                "bot": {
                    "strategy": bot.get("strategy"),
                    "timeframe": timeframe,
                    "symbol": symbol,
                },
                "recent_logs": logs[:8],
                "related_insights": related_insights,
                "related_trades": related_trades,
                "vision_report": vision_report,
                "regime": regime_context,
                "anomaly": anomaly_context,
                "correlated_context": correlated_context,
                "events": events_context,
            }
            narrative, _model, llm_provider = await summarize_trade_explain(bundle, model=llm_model)
        except Exception:
            narrative = None

    if use_llm and not narrative:
        narrative = summary

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
        "vision_report": vision_report,
        "regime": regime_context,
        "anomaly": anomaly_context,
        "correlated_context": correlated_context,
        "events": events_context,
        "narrative": narrative,
        "llm_provider": llm_provider,
        "sources": [
            s for s, ok in [
                ("bot_trades", bool(trade)),
                ("agent_insights", bool(insight)),
                ("bot_logs", bool(logs)),
                ("related_insights", bool(related_insights)),
                ("related_trades", bool(related_trades)),
                ("vision_report", bool(vision_report)),
                ("regime", bool(regime_context)),
                ("anomaly", bool(anomaly_context)),
                ("correlated_context", bool(correlated_context and correlated_context.get("group"))),
                ("events", bool(events_context and (
                    events_context.get("corporate") or events_context.get("economic")
                ))),
            ] if ok
        ],
    }
