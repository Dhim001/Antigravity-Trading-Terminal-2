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
# Cap trade samples so Lab Trades/Performance work without shipping full N×logs.
PORTFOLIO_TRADES_PER_SYMBOL = 50
PORTFOLIO_MAX_TRADES_TOTAL = 200


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


def _slim_trade(trade: dict, symbol: str) -> dict:
    """Keep fields needed for Lab trade log + summary aggregates."""
    return {
        "symbol": symbol,
        "time": trade.get("time"),
        "side": trade.get("side"),
        "quantity": trade.get("quantity"),
        "price": trade.get("price"),
        "pnl": trade.get("pnl"),
        "is_exit": trade.get("is_exit"),
        "reason": trade.get("reason") or trade.get("trigger_type"),
        "position_side": trade.get("position_side"),
        "hold_seconds": trade.get("hold_seconds"),
        "mfe_pct": trade.get("mfe_pct"),
        "mae_pct": trade.get("mae_pct"),
    }


def _cap_symbol_trades(trades: list | None, symbol: str) -> list[dict]:
    if not isinstance(trades, list) or not trades:
        return []
    # Prefer recent fills (matches wire trim of single-symbol runs).
    recent = trades[-PORTFOLIO_TRADES_PER_SYMBOL:]
    return [_slim_trade(t, symbol) for t in recent if isinstance(t, dict)]


def _merge_portfolio_trades(per_symbol: dict[str, dict]) -> list[dict]:
    merged: list[dict] = []
    for sym, row in per_symbol.items():
        if not isinstance(row, dict) or row.get("skipped") or row.get("error"):
            continue
        for t in row.get("trades") or []:
            if isinstance(t, dict):
                merged.append(t if t.get("symbol") else {**t, "symbol": sym})
    merged.sort(key=lambda t: int(t.get("time") or 0))
    if len(merged) > PORTFOLIO_MAX_TRADES_TOTAL:
        merged = merged[-PORTFOLIO_MAX_TRADES_TOTAL:]
    return merged


def _closed_trades_for_summary(trades: list[dict]) -> list[dict]:
    """Exits with pnl — same input shape as Backtester._compute_summary."""
    closed = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        if t.get("is_exit") and t.get("pnl") is not None:
            closed.append(t)
        elif t.get("pnl") is not None and t.get("is_exit") is None:
            # Some slim paths only keep exit rows
            closed.append(t)
    return closed


def _enrich_portfolio_summary(
    *,
    total_pnl: float,
    win_rate: float,
    max_drawdown: float,
    trade_count: int,
    starting_capital: float,
    equity_curve: list | None,
    trades: list[dict],
    blocked_entries: int = 0,
) -> dict:
    from app.services.bots.backtester import _compute_summary

    closed = _closed_trades_for_summary(trades)
    # Prefer actual closed sample count for expectancy/avg; keep headline trade_count.
    summary = _compute_summary(
        closed,
        total_pnl=total_pnl,
        win_rate=win_rate,
        max_drawdown=max_drawdown,
        trade_count=trade_count if trade_count else len(closed),
        starting_equity=starting_capital,
        equity_curve=equity_curve or [],
        blocked_entries=blocked_entries,
    )
    # If we only have a capped sample, avg/expectancy still reflect sample — note it.
    if trade_count and closed and len(closed) < trade_count:
        summary["trades_sample_size"] = len(closed)
        summary["trades_sampled"] = True
    return summary


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
       or with resolve_candles= (streamed load — MEMORY_CENTRIC_REVIEW #8)
    2. Legacy: run_portfolio_backtest(run_backtest=fn, symbols=[...], strategy=..., config=..., resolve_candles=fn)
    """
    if backtester is not None and portfolio_config is not None and (
        candles_by_symbol is not None or resolve_candles is not None
    ):
        return _run_portfolio(
            backtester,
            portfolio_config,
            candles_by_symbol,
            resolve_candles=resolve_candles,
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

    total_pnl = float(raw.get("total_pnl") or 0)
    abs_pnl_sum = sum(
        abs(float(r.get("total_pnl") or 0))
        for r in per_symbol.values()
        if isinstance(r, dict) and not (r.get("skipped") or r.get("error"))
    )

    for sym, r in per_symbol.items():
        row = {"symbol": sym}
        if r.get("skipped") or r.get("error"):
            err = r.get("error") or "Skipped"
            row["error"] = err
            skipped_symbols.append({"symbol": sym, "reason": err})
        else:
            sym_pnl = float(r.get("total_pnl") or 0)
            contribution = (
                round(sym_pnl / abs_pnl_sum * 100, 1) if abs_pnl_sum > 1e-9 else 0.0
            )
            row.update({
                "total_pnl": r.get("total_pnl", 0),
                "trade_count": r.get("trade_count", 0),
                "win_rate": r.get("win_rate", 0),
                "sharpe_ratio": r.get("sharpe_ratio"),
                "max_drawdown": r.get("max_drawdown"),
                "weight": r.get("weight"),
                "allocation": r.get("allocation"),
                "pnl_contribution_pct": contribution,
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
    win_rate = float(raw.get("portfolio_win_rate") or 0)

    deployed_capital = sum(
        float(r.get("allocation") or 0)
        for r in per_symbol.values()
        if not (r.get("skipped") or r.get("error"))
    )
    starting_capital = deployed_capital if deployed_capital > 0 else float(raw.get("starting_capital") or 0)
    ending_capital = round(starting_capital + total_pnl, 2)
    return_pct = round(total_pnl / starting_capital * 100, 2) if starting_capital > 0 else 0
    max_drawdown = float(raw.get("max_drawdown") or 0)
    trade_count = int(raw.get("total_trades") or 0)

    equity_curve = raw.get("equity_curve")
    if equity_curve and isinstance(equity_curve, list) and equity_curve and isinstance(equity_curve[0], (int, float)):
        equity_curve = _combine_equity_curves(per_symbol, raw.get("starting_capital", 0))
    if not equity_curve:
        equity_curve = _combine_equity_curves(per_symbol, starting_capital)

    trades = raw.get("trades")
    if not isinstance(trades, list):
        trades = _merge_portfolio_trades(per_symbol)

    blocked_entries = sum(
        int(r.get("blocked_entries") or 0)
        for r in per_symbol.values()
        if isinstance(r, dict) and not r.get("skipped")
    )

    summary = _enrich_portfolio_summary(
        total_pnl=total_pnl,
        win_rate=win_rate,
        max_drawdown=max_drawdown,
        trade_count=trade_count,
        starting_capital=starting_capital,
        equity_curve=equity_curve if isinstance(equity_curve, list) else [],
        trades=trades,
        blocked_entries=blocked_entries,
    )
    # Prefer portfolio headline return/win rate over sample-derived if present
    summary["return_pct"] = return_pct
    summary["win_rate"] = win_rate
    summary["total_trades"] = trade_count
    summary["total_pnl"] = round(total_pnl, 2)
    summary["max_drawdown"] = max_drawdown

    from app.services.bots.backtest_analytics import drawdown_curve as _dd_curve

    drawdown = raw.get("drawdown_curve")
    if not isinstance(drawdown, list) or not drawdown:
        drawdown = _dd_curve(equity_curve if isinstance(equity_curve, list) else [])

    return {
        "portfolio": True,
        "total_pnl": round(total_pnl, 2),
        "trade_count": trade_count,
        "trades_total": trade_count,
        "win_rate": win_rate,
        "max_drawdown": max_drawdown,
        "return_pct": return_pct,
        "starting_capital": starting_capital,
        "ending_capital": ending_capital,
        "starting_equity": starting_capital,
        "allocation": starting_capital,
        "equity_curve": equity_curve,
        "drawdown_curve": drawdown,
        "trades": trades,
        "symbol_results": symbol_results,
        "symbols_tested": active_count,
        "symbols_failed": failed_count,
        "symbols_traded": active_count,
        "skipped_symbols": skipped_symbols,
        "correlation_summary": correlation_summary,
        "summary": summary,
        "per_symbol": {
            sym: {k: v for k, v in r.items() if k not in ("equity_curve", "trades")}
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
        summary = result.get("summary") or {}
        per_symbol[sym] = {
            "total_pnl": result.get("total_pnl", 0),
            "trade_count": result.get("trade_count", 0),
            "win_rate": result.get("win_rate", 0),
            "max_drawdown": result.get("max_drawdown", 0),
            "sharpe_ratio": summary.get("sharpe_ratio"),
            "blocked_entries": summary.get("blocked_entries") or 0,
            "weight": round(weight, 4),
            "allocation": round(sym_capital, 2),
            "equity_curve": result.get("equity_curve", []),
            "trades": _cap_symbol_trades(result.get("trades"), sym),
        }

    total_pnl = sum(r.get("total_pnl", 0) for r in per_symbol.values() if not r.get("skipped"))
    total_trades = sum(r.get("trade_count", 0) for r in per_symbol.values() if not r.get("skipped"))
    total_wins = sum(
        r.get("trade_count", 0) * r.get("win_rate", 0) / 100
        for r in per_symbol.values()
        if not r.get("skipped") and r.get("trade_count", 0) > 0
    )
    portfolio_win_rate = (
        round(total_wins / total_trades * 100, 2) if total_trades > 0 else 0.0
    )
    equity_curve = _combine_equity_curves(per_symbol, total_capital)
    peak = equity_curve[0]["equity"] if equity_curve else total_capital
    max_dd = 0.0
    for pt in equity_curve:
        val = float(pt.get("equity") or 0)
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    raw = {
        "total_pnl": round(total_pnl, 2),
        "total_trades": total_trades,
        "portfolio_win_rate": portfolio_win_rate,
        "max_drawdown": round(max_dd * 100, 2),
        "starting_capital": total_capital,
        "ending_capital": round(total_capital + total_pnl, 2),
        "return_pct": round(total_pnl / total_capital * 100, 2) if total_capital > 0 else 0,
        "per_symbol": per_symbol,
        "equity_curve": equity_curve,
        "trades": _merge_portfolio_trades(per_symbol),
    }
    return format_portfolio_results(raw)


def _run_portfolio(
    backtester,
    portfolio_config: PortfolioBacktestConfig,
    candles_by_symbol: dict[str, list] | None,
    *,
    resolve_candles: Callable | None = None,
    progress_cb=None,
    cancel_cb=None,
) -> dict[str, Any]:
    """Run a multi-symbol portfolio backtest with shared capital.

    MEMORY_CENTRIC_REVIEW #8 — stream candles in worker-sized batches:
    load → run → keep slim result → release candles before the next batch.
    Peak ≈ workers × 1 symbol instead of all symbols resident at once.
    """
    cfg = portfolio_config
    total_capital = cfg.total_capital
    per_symbol_results: dict[str, dict] = {}
    preloaded = candles_by_symbol if isinstance(candles_by_symbol, dict) else {}

    total_weight = sum(s.get("weight", 1.0) for s in cfg.symbols)
    n_symbols = len(cfg.symbols)
    workers = parallel_worker_count(n_symbols)
    run_bt = thread_local_backtest_runner(backtester) if workers > 1 else backtester.run_backtest

    def _load_candles(symbol: str) -> list:
        if resolve_candles is not None:
            try:
                resolved = resolve_candles(symbol)
            except Exception as exc:
                logger.warning("Portfolio candle resolve failed for %s: %s", symbol, exc)
                return []
            if isinstance(resolved, tuple):
                candles = resolved[0]
            else:
                candles = resolved
            return candles or []
        return preloaded.get(symbol, []) or []

    def _run_symbol(idx: int, sym_cfg: dict, candles: list) -> tuple[int, str, dict]:
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
            "blocked_entries": (result.get("summary") or {}).get("blocked_entries") or 0,
            "weight": round(weight, 4),
            "allocation": round(symbol_capital, 2),
            "equity_curve": result.get("equity_curve", []),
            "trades": _cap_symbol_trades(result.get("trades"), symbol),
        }

    completed = 0
    batch_size = max(1, workers)

    for batch_start in range(0, n_symbols, batch_size):
        if cancel_cb and cancel_cb():
            return {"error": "cancelled", "cancelled": True}

        batch = list(enumerate(cfg.symbols))[batch_start : batch_start + batch_size]
        # Resolve on this thread before submit — avoid concurrent resolve races.
        chunk_candles: dict[str, list] = {}
        for idx, sym_cfg in batch:
            symbol = sym_cfg["symbol"]
            chunk_candles[symbol] = _load_candles(symbol)

        if workers > 1 and len(batch) > 1:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bt-portfolio") as pool:
                futures = [
                    pool.submit(
                        _run_symbol,
                        idx,
                        sym_cfg,
                        chunk_candles.get(sym_cfg["symbol"], []),
                    )
                    for idx, sym_cfg in batch
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
                        skipped=(
                            [{"symbol": symbol, "reason": row["error"]}]
                            if row.get("skipped")
                            else []
                        ),
                    )
        else:
            for idx, sym_cfg in batch:
                if cancel_cb and cancel_cb():
                    return {"error": "cancelled", "cancelled": True}
                symbol = sym_cfg["symbol"]
                _, _, row = _run_symbol(idx, sym_cfg, chunk_candles.get(symbol, []))
                if row.get("cancelled"):
                    return {"error": "cancelled", "cancelled": True}
                per_symbol_results[symbol] = row
                completed += 1
                _notify_progress(
                    progress_cb,
                    symbol_index=completed,
                    symbol_total=n_symbols,
                    symbol=symbol,
                    skipped=(
                        [{"symbol": symbol, "reason": row["error"]}]
                        if row.get("skipped")
                        else []
                    ),
                )

        # Release this batch's candle lists before loading the next.
        chunk_candles.clear()

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
    merged_trades = _merge_portfolio_trades(per_symbol_results)

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
        "trades": merged_trades,
        "symbol_count": len(cfg.symbols),
        "symbols_traded": len(active_symbols),
    }
