"""Extended backtest analytics — benchmark, Sortino, drawdown curve."""

from __future__ import annotations

from typing import Any


def sortino_ratio(equity_curve: list[dict]) -> float | None:
    if len(equity_curve) < 3:
        return None
    returns: list[float] = []
    for j in range(1, len(equity_curve)):
        prev_eq = equity_curve[j - 1].get("equity")
        curr_eq = equity_curve[j].get("equity")
        if prev_eq and prev_eq > 0 and curr_eq is not None:
            returns.append((float(curr_eq) - float(prev_eq)) / float(prev_eq))
    if len(returns) < 2:
        return None
    mean_r = sum(returns) / len(returns)
    downside = [min(0.0, r) for r in returns]
    down_var = sum(d ** 2 for d in downside) / len(downside)
    down_std = down_var ** 0.5
    if down_std < 1e-12:
        return None
    t0 = equity_curve[0].get("time")
    t1 = equity_curve[-1].get("time")
    if t0 and t1 and t1 > t0:
        years = (int(t1) - int(t0)) / (365.25 * 86400)
        if years > 0:
            return round((mean_r / down_std) * (len(returns) / years) ** 0.5, 2)
    return round((mean_r / down_std) * (len(returns) ** 0.5), 2)


def buy_and_hold_benchmark(candles: list[dict], starting_equity: float) -> dict[str, Any] | None:
    if not candles or len(candles) < 2 or starting_equity <= 0:
        return None
    first = float(candles[0].get("close") or candles[0].get("open") or 0)
    last = float(candles[-1].get("close") or 0)
    if first <= 0 or last <= 0:
        return None
    shares = starting_equity / first
    pnl = round(shares * (last - first), 2)
    ret_pct = round((last - first) / first * 100, 2)
    return {
        "return_pct": ret_pct,
        "pnl": pnl,
        "entry_price": round(first, 4),
        "exit_price": round(last, 4),
    }


def drawdown_curve(equity_curve: list[dict]) -> list[dict]:
    peak = 0.0
    out: list[dict] = []
    for point in equity_curve or []:
        eq = float(point.get("equity") or 0)
        peak = max(peak, eq)
        dd = ((peak - eq) / peak * 100) if peak > 0 else 0.0
        out.append({
            "time": point.get("time"),
            "drawdown_pct": round(dd, 2),
        })
    return out


def enrich_summary(summary: dict, *, equity_curve: list[dict], candles: list[dict], starting_equity: float) -> dict:
    """Attach P4 analytics fields to an existing summary dict."""
    enriched = dict(summary)
    enriched["sortino_ratio"] = sortino_ratio(equity_curve)
    bench = buy_and_hold_benchmark(candles, starting_equity)
    if bench:
        enriched["benchmark"] = bench
        strat_pnl = float(summary.get("total_pnl") or 0)
        enriched["alpha_pnl"] = round(strat_pnl - float(bench["pnl"]), 2)
        strat_ret = float(summary.get("return_pct") or 0)
        enriched["alpha_return_pct"] = round(strat_ret - float(bench["return_pct"]), 2)
    return enriched
