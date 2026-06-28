"""Portfolio analytics — aggregates bot trades + account trade history."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal

from app.config import PORTFOLIO_MAX_GROSS_EXPOSURE_PCT, PORTFOLIO_MAX_GROUP_EXPOSURE_PCT
from app.database import get_connection
from app.services.bots import analytics as bot_analytics
from app.services.bots.portfolio_risk import build_portfolio_snapshot, list_bot_exposures, symbol_correlation_group

SourceFilter = Literal["bot", "account", "combined"]
GroupBy = Literal["strategy", "symbol", "timeframe"]

MANUAL_STRATEGY = "Manual"
MANUAL_TIMEFRAME = "—"


def _parse_timestamp(ts) -> float | None:
    """Return unix seconds."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        v = float(ts)
        return v / 1000.0 if v > 1e12 else v
    if isinstance(ts, str):
        try:
            raw = ts.strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            if " " in raw and "T" not in raw:
                raw = raw.replace(" ", "T", 1)
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def _period_cutoff(period: str | int | None) -> float:
    """Unix seconds cutoff; 0 = all time."""
    if not period or str(period).upper() == "ALL":
        return 0.0
    days_map = {"1D": 1, "1W": 7, "1M": 30}
    if isinstance(period, str):
        days = days_map.get(period.upper())
        if days is None:
            try:
                days = int(period)
            except ValueError:
                return 0.0
    else:
        days = int(period)
    return (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()


def _compute_trade_stats(pnls: list[float]) -> dict:
    exits = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_count = len(wins)
    total_pnl = sum(pnls)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 2)
    elif gross_profit > 0:
        profit_factor = None
    else:
        profit_factor = 0.0
    win_rate = round((win_count / exits) * 100, 1) if exits else 0.0
    expectancy = round(total_pnl / exits, 2) if exits else 0.0
    return {
        "trade_count": exits,
        "exit_count": exits,
        "win_count": win_count,
        "win_rate": win_rate,
        "total_pnl": round(total_pnl, 2),
        "expectancy": expectancy,
        "profit_factor": profit_factor,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
    }


def _fetch_bot_exit_trades(cutoff: float) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT t.id, t.bot_id, t.symbol, t.pnl, t.timestamp,
               b.strategy, b.timeframe
        FROM bot_trades t
        JOIN bots b ON b.id = t.bot_id
        WHERE t.is_exit = 1 AND t.pnl IS NOT NULL
        ORDER BY t.timestamp ASC
        """
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    out = []
    for row in rows:
        ts = _parse_timestamp(row["timestamp"])
        if ts is None or ts < cutoff:
            continue
        out.append({
            "source": "bot",
            "id": row["id"],
            "bot_id": row["bot_id"],
            "symbol": row["symbol"],
            "strategy": row["strategy"],
            "timeframe": row["timeframe"],
            "pnl": float(row["pnl"]),
            "timestamp": ts,
        })
    return out


def _fetch_account_exit_trades(account_history: dict | list, cutoff: float) -> list[dict]:
    if isinstance(account_history, dict):
        trades = account_history.get("trades") or []
    elif isinstance(account_history, list):
        trades = account_history
    else:
        trades = []
    out = []
    for t in trades:
        if t.get("status") != "FILLED" or t.get("side") != "SELL":
            continue
        pnl = t.get("realized_pnl")
        if pnl is None:
            continue
        ts = _parse_timestamp(t.get("timestamp"))
        if ts is None or ts < cutoff:
            continue
        out.append({
            "source": "account",
            "id": t.get("id"),
            "bot_id": None,
            "symbol": t.get("symbol"),
            "strategy": MANUAL_STRATEGY,
            "timeframe": MANUAL_TIMEFRAME,
            "pnl": float(pnl),
            "timestamp": ts,
        })
    return out


def _filter_source(trades: list[dict], source: SourceFilter) -> list[dict]:
    if source == "combined":
        return trades
    return [t for t in trades if t["source"] == source]


def collect_exit_trades(
    account_history: dict | list,
    *,
    period: str | int | None = None,
    source: SourceFilter = "combined",
) -> list[dict]:
    cutoff = _period_cutoff(period)
    bot = _fetch_bot_exit_trades(cutoff)
    acct = _fetch_account_exit_trades(account_history, cutoff)
    combined = bot + acct
    combined.sort(key=lambda t: t["timestamp"])
    return _filter_source(combined, source)


def get_portfolio_equity_curve(
    account_history: dict | list,
    *,
    period: str | int | None = None,
    source: SourceFilter = "combined",
) -> dict:
    trades = collect_exit_trades(account_history, period=period, source=source)
    cum = 0.0
    series = []
    for t in trades:
        cum += t["pnl"]
        series.append({
            "time": int(t["timestamp"]),
            "value": round(cum, 2),
            "source": t["source"],
        })
    stats = _compute_trade_stats([t["pnl"] for t in trades])
    return {"series": series, "stats": stats, "source": source, "period": period or "ALL"}


def get_breakdown_stats(
    account_history: dict | list,
    group_by: GroupBy,
    *,
    period: str | int | None = None,
    source: SourceFilter = "combined",
) -> dict:
    trades = collect_exit_trades(account_history, period=period, source=source)
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        key = t.get(group_by) or "Unknown"
        buckets[str(key)].append(t["pnl"])

    rows = []
    for key, pnls in sorted(buckets.items(), key=lambda kv: sum(kv[1]), reverse=True):
        row = _compute_trade_stats(pnls)
        row["key"] = key
        row["group_by"] = group_by
        rows.append(row)
    return {"rows": rows, "group_by": group_by, "source": source, "period": period or "ALL"}


def _day_key(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def get_daily_pnl_calendar(
    account_history: dict | list,
    *,
    start: str | None = None,
    end: str | None = None,
    source: SourceFilter = "combined",
) -> dict:
    trades = collect_exit_trades(account_history, period=None, source=source)
    if start:
        start_ts = _parse_timestamp(start) or 0.0
        trades = [t for t in trades if t["timestamp"] >= start_ts]
    if end:
        end_ts = _parse_timestamp(end) or float("inf")
        trades = [t for t in trades if t["timestamp"] <= end_ts]

    daily: dict[str, float] = defaultdict(float)
    for t in trades:
        daily[_day_key(t["timestamp"])] += t["pnl"]

    days = sorted(daily.keys())
    cells = [{"date": d, "pnl": round(daily[d], 2)} for d in days]
    return {"days": cells, "source": source}


def get_allocation(oms) -> dict:
    """Current notional allocation by symbol and strategy."""
    snapshot = build_portfolio_snapshot(oms)
    symbol_slices = [
        {"symbol": sym, "notional": round(val, 2)}
        for sym, val in sorted(snapshot.symbol_exposure.items(), key=lambda kv: kv[1], reverse=True)
        if val > 0
    ]
    total = sum(s["notional"] for s in symbol_slices) or 1.0
    for s in symbol_slices:
        s["pct"] = round((s["notional"] / total) * 100, 2)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT bp.symbol, bp.size, bp.avg_price, b.strategy, b.allocation
        FROM bot_positions bp
        JOIN bots b ON b.id = bp.bot_id
        WHERE ABS(bp.size) > 1e-8
        """
    )
    bot_rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    marks = snapshot.symbol_exposure
    strategy_notional: dict[str, float] = defaultdict(float)
    for row in bot_rows:
        sym = row["symbol"]
        mark = marks.get(sym) or float(row["avg_price"] or 0)
        strategy_notional[row["strategy"]] += abs(float(row["size"]) * mark)

    account = oms.get_account_data()
    manual_notional = 0.0
    for sym, pos in (account.get("positions") or {}).items():
        size = float(pos.get("size") or 0)
        if abs(size) < 1e-8:
            continue
        mark = marks.get(sym) or float(pos.get("avg_price") or 0)
        manual_notional += abs(size * mark)
    if manual_notional > 0:
        strategy_notional[MANUAL_STRATEGY] += manual_notional

    strat_total = sum(strategy_notional.values()) or 1.0
    strategy_slices = [
        {
            "strategy": strat,
            "notional": round(val, 2),
            "pct": round((val / strat_total) * 100, 2),
        }
        for strat, val in sorted(strategy_notional.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return {
        "by_symbol": symbol_slices,
        "by_strategy": strategy_slices,
        "total_notional": round(total, 2),
        "account_equity": round(snapshot.account_equity, 2),
    }



def get_correlation_matrix(
    account_history: dict | list,
    *,
    period: str | int | None = "1M",
    source: SourceFilter = "combined",
    symbols: list[str] | None = None,
    mode: str = "auto",
    feed=None,
    oms=None,
) -> dict:
    mode = (mode or "auto").lower()
    from app.config import RISK_DYNAMIC_CORRELATION_ENABLED
    from app.services.bots.correlation import get_price_correlation_matrix, get_trade_pnl_correlation_matrix

    use_price = mode == "price" or (mode == "auto" and RISK_DYNAMIC_CORRELATION_ENABLED)
    if use_price:
        price_result = get_price_correlation_matrix(symbols=symbols, feed=feed)
        if price_result.get("matrix"):
            return price_result
        if mode == "price":
            return price_result

    trades = collect_exit_trades(account_history, period=period, source=source)
    account_equity = 0.0
    if oms is not None:
        try:
            account_equity = float(build_portfolio_snapshot(oms).account_equity)
        except Exception:
            account_equity = 0.0

    return get_trade_pnl_correlation_matrix(
        trades,
        period=period,
        symbols=symbols,
        account_equity=account_equity,
    )


def get_bot_rankings(*, limit: int = 10) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, strategy, symbol, timeframe, status FROM bots")
    bots = {row["id"]: dict(row) for row in cursor.fetchall()}
    conn.close()

    bot_ids = list(bots.keys())
    stats = bot_analytics.get_all_bot_stats(bot_ids)

    rows = []
    for bot_id, meta in bots.items():
        st = stats.get(bot_id, {})
        rows.append({
            "bot_id": bot_id,
            "strategy": meta.get("strategy"),
            "symbol": meta.get("symbol"),
            "timeframe": meta.get("timeframe"),
            "status": meta.get("status"),
            **st,
        })

    ranked = sorted(rows, key=lambda r: r.get("total_pnl") or 0, reverse=True)
    top = ranked[:limit]
    bottom = list(reversed(ranked[-limit:])) if len(ranked) > limit else []
    return {"top": top, "bottom": bottom, "total_bots": len(rows)}


def get_risk_utilization(oms) -> dict:
    from app.services.bots.risk_monitor import compute_drawdown, drawdown_to_dict

    snapshot = build_portfolio_snapshot(oms)
    equity = max(snapshot.account_equity, 1.0)
    max_gross = equity * (PORTFOLIO_MAX_GROSS_EXPOSURE_PCT / 100.0)
    gross_pct = round((snapshot.gross_exposure / max_gross) * 100, 1) if max_gross else 0.0

    groups = []
    for group, exp in snapshot.group_exposure.items():
        cap = equity * (PORTFOLIO_MAX_GROUP_EXPOSURE_PCT / 100.0)
        groups.append({
            "group": group,
            "exposure": round(exp, 2),
            "cap": round(cap, 2),
            "utilization_pct": round((exp / cap) * 100, 1) if cap else 0.0,
        })

    bot_rows = list_bot_exposures()
    drawdown = drawdown_to_dict(compute_drawdown(oms))
    from app.services.bots.time_windows import time_controls_status
    from app.services.bots.position_duration import position_duration_status
    from app.services.bots.correlation import correlation_status
    from app.services.bots.margin_risk import build_margin_snapshot, margin_status, margin_to_dict

    margin = build_margin_snapshot(oms, snapshot)
    margin_dict = margin_to_dict(margin)

    return {
        "account_equity": round(equity, 2),
        "gross_exposure": round(snapshot.gross_exposure, 2),
        "gross_cap": round(max_gross, 2),
        "gross_utilization_pct": min(gross_pct, 999.0),
        "max_gross_pct": PORTFOLIO_MAX_GROSS_EXPOSURE_PCT,
        "max_group_pct": PORTFOLIO_MAX_GROUP_EXPOSURE_PCT,
        "groups": sorted(groups, key=lambda g: g["utilization_pct"], reverse=True),
        "open_bot_positions": len(bot_rows),
        "time_controls": time_controls_status(),
        "position_duration": position_duration_status(),
        "dynamic_correlation": correlation_status(),
        "margin": margin_dict,
        "margin_enabled": margin_dict.get("enabled", False),
        "margin_utilization_pct": margin_dict.get("utilization_pct", 0.0),
        "margin_used": margin_dict.get("margin_used", 0.0),
        "margin_capacity": margin_dict.get("margin_capacity", equity),
        "max_margin_utilization_pct": margin_dict.get("max_utilization_pct"),
        "max_leverage_cap": margin_dict.get("max_leverage_cap"),
        "margin_config": margin_status(),
        **drawdown,
    }
