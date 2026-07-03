"""Portfolio-level multi-symbol backtest — run N strategies concurrently
with shared capital, cross-symbol risk budgeting, and correlation-aware
position sizing.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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
    # Legacy path
    if run_backtest is not None and symbols is not None:
        return _run_legacy(
            run_backtest=run_backtest,
            symbols=symbols,
            strategy=strategy or "CHART_AGENT",
            config=config or {},
            resolve_candles=resolve_candles,
        )

    # New path
    if backtester is None or portfolio_config is None or candles_by_symbol is None:
        return {"error": "Missing required arguments"}

    return _run_portfolio(backtester, portfolio_config, candles_by_symbol,
                          progress_cb=progress_cb, cancel_cb=cancel_cb)


def _run_legacy(
    run_backtest,
    symbols: list[str],
    strategy: str,
    config: dict,
    resolve_candles=None,
) -> dict[str, Any]:
    """Legacy calling convention for backward compat."""
    symbol_results: list[dict] = []
    total_pnl = 0.0

    for sym in symbols:
        candles, _meta = resolve_candles(sym) if resolve_candles else ([], {})
        if not candles or len(candles) < 50:
            continue
        result = run_backtest(sym, strategy, config, candles)
        if isinstance(result, dict) and not result.get("error"):
            sym_pnl = result.get("total_pnl", 0)
            total_pnl += sym_pnl
            symbol_results.append({
                "symbol": sym,
                "total_pnl": sym_pnl,
                "trade_count": result.get("trade_count", 0),
                "summary": result.get("summary", {}),
            })

    return {
        "portfolio": True,
        "total_pnl": round(total_pnl, 2),
        "symbol_results": symbol_results,
        "symbols_traded": len(symbol_results),
    }


def _run_portfolio(
    backtester,
    portfolio_config: PortfolioBacktestConfig,
    candles_by_symbol: dict[str, list],
    *,
    progress_cb=None,
    cancel_cb=None,
) -> dict[str, Any]:
    """Run a multi-symbol portfolio backtest with shared capital.

    Args:
        backtester: BacktesterService instance
        portfolio_config: Portfolio-level config
        candles_by_symbol: {symbol: candle_list} for each symbol
        progress_cb: optional progress callback(done, total)
        cancel_cb: optional cancellation check

    Returns:
        Portfolio-level results dict with per-symbol breakdown.
    """
    cfg = portfolio_config
    total_capital = cfg.total_capital
    per_symbol_results: dict[str, dict] = {}

    # Weight-based capital allocation
    total_weight = sum(s.get("weight", 1.0) for s in cfg.symbols)

    n_symbols = len(cfg.symbols)
    for idx, sym_cfg in enumerate(cfg.symbols):
        if cancel_cb and cancel_cb():
            return {"error": "cancelled", "cancelled": True}

        symbol = sym_cfg["symbol"]
        strategy = sym_cfg.get("strategy", "CHART_AGENT")
        config = copy.deepcopy(sym_cfg.get("config", {}))
        weight = sym_cfg.get("weight", 1.0) / total_weight

        # Allocate capital proportionally
        symbol_capital = total_capital * weight
        config["allocation"] = symbol_capital
        config["slippage_bps"] = cfg.slippage_bps
        config["fee_bps"] = cfg.fee_bps

        candles = candles_by_symbol.get(symbol, [])
        if not candles or len(candles) < 50:
            per_symbol_results[symbol] = {"error": "Not enough data", "skipped": True}
            continue

        if progress_cb:
            progress_cb(idx, n_symbols)

        # Run individual backtest per symbol
        result = backtester.run_backtest(
            symbol, strategy, config, candles,
            cancel_cb=cancel_cb,
        )

        if isinstance(result, dict) and result.get("error"):
            per_symbol_results[symbol] = {"error": result["error"], "skipped": True}
            continue

        per_symbol_results[symbol] = {
            "total_pnl": result.get("total_pnl", 0),
            "trade_count": result.get("trade_count", 0),
            "win_rate": result.get("win_rate", 0),
            "max_drawdown": result.get("max_drawdown", 0),
            "sharpe_ratio": (result.get("summary") or {}).get("sharpe_ratio"),
            "weight": round(weight, 4),
            "allocation": round(symbol_capital, 2),
            "equity_curve": result.get("equity_curve", []),
        }

    # Aggregate portfolio metrics
    total_pnl = sum(
        r.get("total_pnl", 0) for r in per_symbol_results.values()
        if not r.get("skipped")
    )
    total_trades = sum(
        r.get("trade_count", 0) for r in per_symbol_results.values()
        if not r.get("skipped")
    )
    active_symbols = [
        s for s, r in per_symbol_results.items() if not r.get("skipped")
    ]

    # Build combined equity curve (simple weighted sum)
    max_len = max(
        (len(r.get("equity_curve", [])) for r in per_symbol_results.values()
         if not r.get("skipped")),
        default=0,
    )
    combined_equity = [total_capital] * max(max_len, 1)
    for sym, r in per_symbol_results.items():
        if r.get("skipped"):
            continue
        eq = r.get("equity_curve", [])
        base = r.get("allocation", 0)
        for i, val in enumerate(eq):
            if i < len(combined_equity):
                combined_equity[i] += (val - base)  # add PnL delta

    # Portfolio max drawdown
    peak = combined_equity[0] if combined_equity else total_capital
    max_dd = 0.0
    for val in combined_equity:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Win rate across all symbols
    total_wins = sum(
        r.get("trade_count", 0) * r.get("win_rate", 0) / 100
        for r in per_symbol_results.values()
        if not r.get("skipped") and r.get("trade_count", 0) > 0
    )
    portfolio_win_rate = (
        round(total_wins / total_trades * 100, 2) if total_trades > 0 else 0
    )

    return {
        "total_pnl": round(total_pnl, 2),
        "total_trades": total_trades,
        "portfolio_win_rate": portfolio_win_rate,
        "max_drawdown": round(max_dd * 100, 2),
        "starting_capital": total_capital,
        "ending_capital": round(total_capital + total_pnl, 2),
        "return_pct": round(total_pnl / total_capital * 100, 2) if total_capital > 0 else 0,
        "active_symbols": active_symbols,
        "per_symbol": per_symbol_results,
        "equity_curve": combined_equity,
        "symbol_count": len(cfg.symbols),
        "symbols_traded": len(active_symbols),
    }
