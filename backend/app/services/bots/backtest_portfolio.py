"""Portfolio-level multi-symbol backtest — run N strategies concurrently
with shared capital, cross-symbol risk budgeting, and correlation-aware
position sizing.
"""

from __future__ import annotations

import copy
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from app.services.bots.backtest_perf import parallel_worker_count
from app.services.bots.backtester import thread_local_backtest_runner

logger = logging.getLogger(__name__)

MIN_BARS = 50
SPARKLINE_POINTS = 24


def _sparkline_from_curve(curve: list | None, max_points: int = SPARKLINE_POINTS) -> list[float]:
    if not curve:
        return []
    values = [float(pt.get("equity") or 0) for pt in curve if isinstance(pt, dict)]
    if not values:
        return []
    if len(values) <= max_points:
        return [round(v, 2) for v in values]
    step = max(1, (len(values) + max_points - 1) // max_points)
    out = [round(values[i], 2) for i in range(0, len(values), step)]
    if out[-1] != round(values[-1], 2):
        out.append(round(values[-1], 2))
    return out


@dataclass
class PortfolioPosition:
    """Tracks one active position in the portfolio backtest."""
    symbol: str
    side: str  # BUY or SELL
    entry_price: float
    quantity: float
    entry_bar: int = 0
    pnl: float = 0.0


@dataclass
class PortfolioBacktestConfig:
    """Configuration for a multi-symbol portfolio backtest."""
    symbols: list[dict] = field(default_factory=list)
    # Each dict: {"symbol": str, "strategy": str, "config": dict, "weight": float}
    total_capital: float = 100_000.0
    max_positions: int = 10
    max_per_symbol: int = 2
    max_correlation_overlap: float = 0.7
    rebalance_interval: int = 0  # 0 = no rebalancing
    slippage_bps: float = 5.0
    fee_bps: float = 10.0


def run_portfolio_backtest(
    backtester=None,
    portfolio_config=None,
    candles_by_symbol=None,
    *,
    # Legacy keyword API for backward compat
    run_backtest=None,
    symbols=None,
    strategy=None,
    config=None,
    resolve_candles=None,
    progress_cb=None,
    cancel_cb=None,
) -> dict[str, Any]:
    """Run a multi-symbol portfolio backtest.

    Supports two calling conventions:
    1. New: run_portfolio_backtest(backtester, PortfolioBacktestConfig, candles_by_symbol)
    2. Legacy: run_portfolio_backtest(run_backtest=fn, symbols=[...], strategy=..., config=..., resolve_candles=fn)
    """
    if backtester is not None and portfolio_config is not None and candles_by_symbol is not None:
        return _run_portfolio(
            backtester,
            portfolio_config,
            candles_by_symbol,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )

    # Legacy path — upgraded with progress/cancel + normalized shape
    if run_backtest is not None and symbols is not None:
        return _run_legacy(
            run_backtest=run_backtest,
            symbols=symbols,
            strategy=strategy or "CHART_AGENT",
            config=config or {},
            resolve_candles=resolve_candles,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )

    return {"error": "Missing required arguments"}


def _notify_progress(
    progress_cb: Callable | None,
    *,
    symbol_index: int,
    symbol_total: int,
    symbol: str,
    skipped: list[dict] | None = None,
) -> None:
    if not progress_cb:
        return
    try:
        progress_cb(
            symbol_index=symbol_index,
            symbol_total=symbol_total,
            symbol=symbol,
            skipped=skipped or [],
        )
    except TypeError:
        progress_cb(symbol_index, symbol_total)


def _combine_equity_curves(
    per_symbol: dict[str, dict],
    total_capital: float,
) -> list[dict]:
    """Time-aligned portfolio equity from per-symbol curves."""
    active = {
        sym: r for sym, r in per_symbol.items()
        if not r.get("skipped") and r.get("equity_curve")
    }
    if not active:
        return [{"time": 0, "equity": round(total_capital, 2)}]

    # Anchor on the symbol with the longest curve
    anchor_sym = max(active, key=lambda s: len(active[s].get("equity_curve") or []))
    anchor_curve = active[anchor_sym]["equity_curve"]
    allocations = {sym: float(r.get("allocation") or 0) for sym, r in active.items()}

    combined: list[dict] = []
    for pt in anchor_curve:
        t = int(pt.get("time") or 0)
        equity = 0.0
        for sym, r in active.items():
            base = allocations.get(sym, 0.0)
            curve = r.get("equity_curve") or []
            # nearest prior point at or before t
            sym_eq = base
            for row in curve:
                rt = int(row.get("time") or 0)
                if rt <= t:
                    sym_eq = float(row.get("equity") or base)
                else:
                    break
            equity += sym_eq
        combined.append({"time": t, "equity": round(equity, 2)})
    return combined


def format_portfolio_results(
    raw: dict[str, Any],
    *,
    correlation_summary: dict | None = None,
) -> dict[str, Any]:
    """Normalize internal portfolio dict for UI + persistence."""
    if raw.get("error") and not raw.get("per_symbol"):
        return raw
    if raw.get("cancelled"):
        return raw

    per_symbol = raw.get("per_symbol") or {}
    symbol_results: list[dict] = []
    skipped_symbols: list[dict] = []

    for sym, r in per_symbol.items():
        row = {"symbol": sym}
        if r.get("skipped") or r.get("error"):
            err = r.get("error") or "Skipped"
            row["error"] = err
            skipped_symbols.append({"symbol": sym, "reason": err})
        else:
            row.update({
                "total_pnl": r.get("total_pnl", 0),
                "trade_count": r.get("trade_count", 0),
                "win_rate": r.get("win_rate", 0),
                "sharpe_ratio": r.get("sharpe_ratio"),
                "max_drawdown": r.get("max_drawdown"),
                "weight": r.get("weight"),
                "allocation": r.get("allocation"),
                "sparkline": r.get("sparkline") or _sparkline_from_curve(r.get("equity_curve")),
            })
        symbol_results.append(row)

    seen_skip: set[str] = set()
    deduped_skipped: list[dict] = []
    for item in skipped_symbols:
        sym = item.get("symbol")
        if sym and sym in seen_skip:
            continue
        if sym:
            seen_skip.add(sym)
        deduped_skipped.append(item)
    skipped_symbols = deduped_skipped

    active_count = sum(1 for r in symbol_results if not r.get("error"))
    failed_count = len(symbol_results) - active_count
    win_rate = raw.get("portfolio_win_rate", 0)

    deployed_capital = sum(
        float(r.get("allocation") or 0)
        for r in per_symbol.values()
        if not (r.get("skipped") or r.get("error"))
    )
    starting_capital = deployed_capital if deployed_capital > 0 else float(raw.get("starting_capital") or 0)
    total_pnl = float(raw.get("total_pnl") or 0)
    ending_capital = round(starting_capital + total_pnl, 2)
    return_pct = round(total_pnl / starting_capital * 100, 2) if starting_capital > 0 else 0

    equity_curve = raw.get("equity_curve")
    if equity_curve and isinstance(equity_curve[0], (int, float)):
        equity_curve = _combine_equity_curves(per_symbol, raw.get("starting_capital", 0))

    return {
        "portfolio": True,
        "total_pnl": raw.get("total_pnl", 0),
        "trade_count": raw.get("total_trades", 0),
        "win_rate": win_rate,
        "max_drawdown": raw.get("max_drawdown", 0),
        "return_pct": return_pct,
        "starting_capital": starting_capital,
        "ending_capital": ending_capital,
        "equity_curve": equity_curve,
        "symbol_results": symbol_results,
        "symbols_tested": active_count,
        "symbols_failed": failed_count,
        "symbols_traded": active_count,
        "skipped_symbols": skipped_symbols,
        "correlation_summary": correlation_summary,
        "summary": {
            "total_pnl": raw.get("total_pnl", 0),
            "total_trades": raw.get("total_trades", 0),
            "win_rate": win_rate,
            "max_drawdown": raw.get("max_drawdown", 0),
            "return_pct": raw.get("return_pct", 0),
        },
        "per_symbol": {
            sym: {k: v for k, v in r.items() if k != "equity_curve"}
            for sym, r in per_symbol.items()
        },
    }


def _run_legacy(
    run_backtest,
    symbols: list[str],
    strategy: str,
    config: dict,
    resolve_candles=None,
    progress_cb=None,
    cancel_cb=None,
) -> dict[str, Any]:
    """Legacy calling convention — normalized output."""
    per_symbol: dict[str, dict] = {}
    total_capital = float(config.get("allocation") or 100_000.0) * len(symbols)
    n_symbols = len(symbols)

    for idx, sym in enumerate(symbols):
        if cancel_cb and cancel_cb():
            return {"error": "cancelled", "cancelled": True}

        candles, _meta = resolve_candles(sym) if resolve_candles else ([], {})
        if not candles or len(candles) < MIN_BARS:
            per_symbol[sym] = {"error": "Not enough data (<50 bars)", "skipped": True}
            _notify_progress(
                progress_cb,
                symbol_index=idx + 1,
                symbol_total=n_symbols,
                symbol=sym,
                skipped=[{"symbol": sym, "reason": "Not enough data"}],
            )
            continue

        _notify_progress(
            progress_cb,
            symbol_index=idx + 1,
            symbol_total=n_symbols,
            symbol=sym,
        )
        result = run_backtest(sym, strategy, config, candles, cancel_cb=cancel_cb)
        if isinstance(result, dict) and result.get("cancelled"):
            return {"error": "cancelled", "cancelled": True}
        if isinstance(result, dict) and result.get("error"):
            per_symbol[sym] = {"error": result["error"], "skipped": True}
            continue

        weight = 1.0 / max(n_symbols, 1)
        sym_capital = total_capital * weight
        per_symbol[sym] = {
            "total_pnl": result.get("total_pnl", 0),
            "trade_count": result.get("trade_count", 0),
            "win_rate": result.get("win_rate", 0),
            "max_drawdown": result.get("max_drawdown", 0),
            "sharpe_ratio": (result.get("summary") or {}).get("sharpe_ratio"),
            "weight": round(weight, 4),
            "allocation": round(sym_capital, 2),
            "equity_curve": result.get("equity_curve", []),
        }

    total_pnl = sum(r.get("total_pnl", 0) for r in per_symbol.values() if not r.get("skipped"))
    total_trades = sum(r.get("trade_count", 0) for r in per_symbol.values() if not r.get("skipped"))
    raw = {
        "total_pnl": round(total_pnl, 2),
        "total_trades": total_trades,
        "portfolio_win_rate": 0.0,
        "max_drawdown": 0.0,
        "starting_capital": total_capital,
        "ending_capital": round(total_capital + total_pnl, 2),
        "return_pct": round(total_pnl / total_capital * 100, 2) if total_capital > 0 else 0,
        "per_symbol": per_symbol,
        "equity_curve": _combine_equity_curves(per_symbol, total_capital),
    }
    return format_portfolio_results(raw)


def _run_portfolio(
    backtester,
    portfolio_config: PortfolioBacktestConfig,
    candles_by_symbol: dict[str, list],
    *,
    progress_cb=None,
    cancel_cb=None,
) -> dict[str, Any]:
    """Run a multi-symbol portfolio backtest with shared capital."""
    cfg = portfolio_config
    total_capital = cfg.total_capital
    per_symbol_results: dict[str, dict] = {}

    total_weight = sum(s.get("weight", 1.0) for s in cfg.symbols)
    n_symbols = len(cfg.symbols)
    workers = parallel_worker_count(n_symbols)
    run_bt = thread_local_backtest_runner(backtester) if workers > 1 else backtester.run_backtest

    def _run_symbol(idx: int, sym_cfg: dict) -> tuple[int, str, dict]:
        if cancel_cb and cancel_cb():
            return idx, sym_cfg["symbol"], {"cancelled": True}

        symbol = sym_cfg["symbol"]
        strategy = sym_cfg.get("strategy", "CHART_AGENT")
        config = copy.deepcopy(sym_cfg.get("config", {}))
        weight = sym_cfg.get("weight", 1.0) / total_weight
        symbol_capital = total_capital * weight
        config["allocation"] = symbol_capital
        config["slippage_bps"] = cfg.slippage_bps
        config["fee_bps"] = cfg.fee_bps

        candles = candles_by_symbol.get(symbol, [])
        if not candles or len(candles) < MIN_BARS:
            return idx, symbol, {"error": "Not enough data (<50 bars)", "skipped": True}

        result = run_bt(
            symbol, strategy, config, candles,
            cancel_cb=cancel_cb,
        )
        if isinstance(result, dict) and result.get("cancelled"):
            return idx, symbol, {"cancelled": True}
        if isinstance(result, dict) and result.get("error"):
            return idx, symbol, {"error": result["error"], "skipped": True}

        return idx, symbol, {
            "total_pnl": result.get("total_pnl", 0),
            "trade_count": result.get("trade_count", 0),
            "win_rate": result.get("win_rate", 0),
            "max_drawdown": result.get("max_drawdown", 0),
            "sharpe_ratio": (result.get("summary") or {}).get("sharpe_ratio"),
            "weight": round(weight, 4),
            "allocation": round(symbol_capital, 2),
            "equity_curve": result.get("equity_curve", []),
        }

    completed = 0

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bt-portfolio") as pool:
            futures = [
                pool.submit(_run_symbol, idx, sym_cfg)
                for idx, sym_cfg in enumerate(cfg.symbols)
            ]
            for fut in as_completed(futures):
                if cancel_cb and cancel_cb():
                    return {"error": "cancelled", "cancelled": True}
                idx, symbol, row = fut.result()
                if row.get("cancelled"):
                    return {"error": "cancelled", "cancelled": True}
                per_symbol_results[symbol] = row
                completed += 1
                _notify_progress(
                    progress_cb,
                    symbol_index=completed,
                    symbol_total=n_symbols,
                    symbol=symbol,
                    skipped=[{"symbol": symbol, "reason": row["error"]}] if row.get("skipped") else [],
                )
    else:
        for idx, sym_cfg in enumerate(cfg.symbols):
            if cancel_cb and cancel_cb():
                return {"error": "cancelled", "cancelled": True}
            symbol = sym_cfg["symbol"]
            _, _, row = _run_symbol(idx, sym_cfg)
            if row.get("cancelled"):
                return {"error": "cancelled", "cancelled": True}
            per_symbol_results[symbol] = row
            _notify_progress(
                progress_cb,
                symbol_index=idx + 1,
                symbol_total=n_symbols,
                symbol=symbol,
                skipped=[{"symbol": symbol, "reason": row["error"]}] if row.get("skipped") else [],
            )

    combined_equity = _combine_equity_curves(per_symbol_results, total_capital)
    for row in per_symbol_results.values():
        if isinstance(row, dict):
            curve = row.get("equity_curve")
            if curve:
                row["sparkline"] = _sparkline_from_curve(curve)
            row.pop("equity_curve", None)

    total_pnl = sum(
        r.get("total_pnl", 0) for r in per_symbol_results.values()
        if not r.get("skipped")
    )
    total_trades = sum(
        r.get("trade_count", 0) for r in per_symbol_results.values()
        if not r.get("skipped")
    )
    active_symbols = [s for s, r in per_symbol_results.items() if not r.get("skipped")]

    peak = combined_equity[0]["equity"] if combined_equity else total_capital
    max_dd = 0.0
    for pt in combined_equity:
        val = float(pt.get("equity") or 0)
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    total_wins = sum(
        r.get("trade_count", 0) * r.get("win_rate", 0) / 100
        for r in per_symbol_results.values()
        if not r.get("skipped") and r.get("trade_count", 0) > 0
    )
    portfolio_win_rate = (
        round(total_wins / total_trades * 100, 2) if total_trades > 0 else 0
    )

    deployed_capital = sum(
        float(r.get("allocation") or 0)
        for r in per_symbol_results.values()
        if not r.get("skipped")
    )
    cap_base = deployed_capital if deployed_capital > 0 else total_capital

    return {
        "total_pnl": round(total_pnl, 2),
        "total_trades": total_trades,
        "portfolio_win_rate": portfolio_win_rate,
        "max_drawdown": round(max_dd * 100, 2),
        "starting_capital": cap_base,
        "ending_capital": round(cap_base + total_pnl, 2),
        "return_pct": round(total_pnl / cap_base * 100, 2) if cap_base > 0 else 0,
        "active_symbols": active_symbols,
        "per_symbol": per_symbol_results,
        "equity_curve": combined_equity,
        "symbol_count": len(cfg.symbols),
        "symbols_traded": len(active_symbols),
    }
