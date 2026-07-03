"""Extended backtest analytics — benchmark, Sortino, drawdown curve, regime tagging."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

ELEVATED_ATR_RATIO = 1.5
COMPRESSED_ATR_RATIO = 0.7
REGIME_MEDIAN_WINDOW = 20
ATR_PERIOD = 14


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


def buy_and_hold_equity_curve(candles: list[dict], starting_equity: float) -> list[dict]:
    """Symbol buy-and-hold equity aligned to candle timestamps."""
    if not candles or starting_equity <= 0:
        return []
    first = float(candles[0].get("close") or candles[0].get("open") or 0)
    if first <= 0:
        return []
    shares = starting_equity / first
    out: list[dict] = []
    for c in candles:
        close = float(c.get("close") or first)
        ts = c.get("time")
        if ts is None:
            continue
        out.append({"time": int(ts), "equity": round(shares * close, 2)})
    return out


def _true_range(candles: list[dict], idx: int) -> float:
    c = candles[idx]
    high = float(c.get("high") or c.get("close") or 0)
    low = float(c.get("low") or c.get("close") or 0)
    if idx <= 0:
        return max(high - low, 0.0)
    prev_close = float(candles[idx - 1].get("close") or 0)
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _atr_series(candles: list[dict], period: int = ATR_PERIOD) -> list[float | None]:
    if not candles:
        return []
    trs = [_true_range(candles, i) for i in range(len(candles))]
    out: list[float | None] = [None] * len(candles)
    if len(trs) < period:
        return out
    seed = sum(trs[:period]) / period
    out[period - 1] = seed
    atr = seed
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i] = atr
    return out


def classify_backtest_regime(candles: list[dict]) -> dict[str, Any]:
    """Tag backtest window by ATR vol regime (matches rule_engine thresholds)."""
    if len(candles) < REGIME_MEDIAN_WINDOW + ATR_PERIOD:
        return {
            "dominant_regime": "unknown",
            "label": "Insufficient history for regime tag",
            "breakdown_pct": {},
            "bar_count": len(candles or []),
        }

    atrs = _atr_series(candles, ATR_PERIOD)
    counts = {"elevated": 0, "normal": 0, "compressed": 0}
    ratios: list[float] = []

    for i in range(REGIME_MEDIAN_WINDOW, len(candles)):
        atr = atrs[i]
        if atr is None or atr <= 0:
            continue
        window = [a for a in atrs[max(0, i - REGIME_MEDIAN_WINDOW + 1): i + 1] if a is not None]
        if not window:
            continue
        sorted_w = sorted(window)
        median_atr = sorted_w[len(sorted_w) // 2]
        ratio = atr / median_atr if median_atr > 0 else 1.0
        ratios.append(ratio)
        if ratio >= ELEVATED_ATR_RATIO:
            counts["elevated"] += 1
        elif ratio <= COMPRESSED_ATR_RATIO:
            counts["compressed"] += 1
        else:
            counts["normal"] += 1

    total = sum(counts.values()) or 1
    dominant = max(counts, key=counts.get)
    breakdown = {k: round(v / total * 100, 1) for k, v in counts.items()}
    median_ratio = round(sorted(ratios)[len(ratios) // 2], 2) if ratios else None
    pct = breakdown.get(dominant, 0)
    return {
        "dominant_regime": dominant,
        "label": f"{dominant.capitalize()} vol ({pct:.0f}% of bars)",
        "breakdown_pct": breakdown,
        "median_atr_ratio": median_ratio,
        "bar_count": total,
    }


def _nearest_close(closes: list[dict], ts: int) -> float | None:
    if not closes:
        return None
    best = closes[0]
    best_diff = abs(int(best["time"]) - ts)
    for row in closes[1:]:
        diff = abs(int(row["time"]) - ts)
        if diff < best_diff:
            best = row
            best_diff = diff
    return float(best.get("close") or 0) or None


def align_benchmark_equity_curve(
    bench_closes: list[dict],
    equity_curve: list[dict],
    starting_equity: float,
) -> list[dict]:
    """Build buy-and-hold equity series aligned to backtest equity timestamps."""
    if not bench_closes or not equity_curve or starting_equity <= 0:
        return []
    first_close = _nearest_close(bench_closes, int(equity_curve[0]["time"]))
    if not first_close or first_close <= 0:
        return []
    shares = starting_equity / first_close
    out: list[dict] = []
    for pt in equity_curve:
        ts = int(pt["time"])
        close = _nearest_close(bench_closes, ts)
        if close is None or close <= 0:
            continue
        out.append({"time": ts, "equity": round(shares * close, 2)})
    return out


def _fetch_benchmark_closes(symbol: str, t0: int, t1: int, feed=None) -> list[dict]:
    from app.services.analytics.benchmarks import DEFAULT_BENCHMARKS, _fetch_yfinance_closes

    days = max(1, (t1 - t0) // 86400 + 2)
    period = f"{days}d" if days <= 59 else "3mo"
    yf_sym = DEFAULT_BENCHMARKS.get(symbol.upper(), symbol)

    closes: list[dict] = []
    if feed and hasattr(feed, "candles") and symbol in getattr(feed, "candles", {}):
        raw = feed.candles.get(symbol) or []
        closes = [
            {"time": int(c["time"]), "close": float(c["close"])}
            for c in raw
            if c.get("close") and t0 - 86400 <= int(c["time"]) <= t1 + 86400
        ]

    if len(closes) < 5:
        interval = "1h" if days <= 7 else "1d"
        closes = _fetch_yfinance_closes(yf_sym, period=period, interval=interval)

    if not closes:
        return []
    return sorted(closes, key=lambda r: r["time"])


def build_backtest_benchmarks(
    *,
    candles: list[dict],
    equity_curve: list[dict],
    starting_equity: float,
    feed=None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Benchmark curves for backtest overlay: symbol B&H + SPY + BTC."""
    out: dict[str, Any] = {
        "symbol_bh_curve": buy_and_hold_equity_curve(candles, starting_equity),
    }
    if not equity_curve:
        return out

    t0 = int(equity_curve[0]["time"])
    t1 = int(equity_curve[-1]["time"])
    for bench_sym in ("SPY", "BTC"):
        try:
            closes = _fetch_benchmark_closes(bench_sym, t0, t1, feed=feed)
            curve = align_benchmark_equity_curve(closes, equity_curve, starting_equity)
            if curve:
                first_eq = curve[0]["equity"]
                last_eq = curve[-1]["equity"]
                out[bench_sym] = {
                    "curve": curve,
                    "return_pct": round((last_eq - first_eq) / first_eq * 100, 2) if first_eq else 0,
                    "pnl": round(last_eq - starting_equity, 2),
                }
        except Exception as exc:
            logger.debug("Benchmark %s skipped: %s", bench_sym, exc)

    sym_upper = (symbol or "").upper()
    if sym_upper and sym_upper not in ("SPY", "BTC", "BTC-USD"):
        bench = buy_and_hold_benchmark(candles, starting_equity)
        if bench:
            out["symbol_bh"] = bench
    return out


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


def enrich_summary(
    summary: dict,
    *,
    equity_curve: list[dict],
    candles: list[dict],
    starting_equity: float,
    feed=None,
    symbol: str | None = None,
) -> dict:
    """Attach P4+ analytics fields to an existing summary dict."""
    enriched = dict(summary)
    enriched["sortino_ratio"] = sortino_ratio(equity_curve)
    bench = buy_and_hold_benchmark(candles, starting_equity)
    if bench:
        enriched["benchmark"] = bench
        strat_pnl = float(summary.get("total_pnl") or 0)
        enriched["alpha_pnl"] = round(strat_pnl - float(bench["pnl"]), 2)
        strat_ret = float(summary.get("return_pct") or 0)
        enriched["alpha_return_pct"] = round(strat_ret - float(bench["return_pct"]), 2)

    regime = classify_backtest_regime(candles)
    enriched["regime"] = regime

    benchmarks = build_backtest_benchmarks(
        candles=candles,
        equity_curve=equity_curve,
        starting_equity=starting_equity,
        feed=feed,
        symbol=symbol,
    )
    if benchmarks:
        enriched["benchmark_overlays"] = benchmarks

    return enriched
