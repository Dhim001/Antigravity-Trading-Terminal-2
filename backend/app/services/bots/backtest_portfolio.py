"""Multi-symbol portfolio backtest — same strategy/config across a symbol set."""

from __future__ import annotations

from typing import Any, Callable


def run_portfolio_backtest(
    *,
    run_backtest: Callable[..., dict],
    symbols: list[str],
    strategy: str,
    config: dict,
    resolve_candles: Callable[[str], tuple[list, dict]],
    progress_cb=None,
    cancel_cb=None,
) -> dict[str, Any]:
    """
    Run independent backtests per symbol and aggregate metrics.
    Each symbol uses the same strategy/config; allocation is per-symbol from config.
    """
    syms = [s.upper() for s in symbols if s]
    if not syms:
        return {"error": "No symbols provided for portfolio backtest"}

    rows: list[dict] = []
    total_pnl = 0.0
    total_trades = 0
    weighted_win = 0.0

    for idx, sym in enumerate(syms):
        if cancel_cb and cancel_cb():
            return {"error": "Backtest cancelled", "cancelled": True}

        try:
            candles, meta = resolve_candles(sym)
        except ValueError as exc:
            rows.append({"symbol": sym, "error": str(exc)})
            continue

        if not candles or len(candles) < 50:
            rows.append({"symbol": sym, "error": "Insufficient candle history"})
            continue

        def _progress(done: int, total: int, *, _sym=sym, _idx=idx) -> None:
            if progress_cb:
                progress_cb(
                    symbol=_sym,
                    symbol_index=_idx + 1,
                    symbol_total=len(syms),
                    bar=done,
                    bars=total,
                )

        result = run_backtest(sym, strategy, config, candles, progress_cb=_progress, cancel_cb=cancel_cb)
        if result.get("cancelled"):
            return {"error": "Backtest cancelled", "cancelled": True}
        if result.get("error"):
            rows.append({"symbol": sym, "error": result["error"]})
            continue

        summary = result.get("summary") or {}
        pnl = float(result.get("total_pnl") or 0)
        tc = int(result.get("trade_count") or 0)
        wr = float(summary.get("win_rate") or 0)
        total_pnl += pnl
        total_trades += tc
        weighted_win += wr * tc
        rows.append({
            "symbol": sym,
            "total_pnl": pnl,
            "trade_count": tc,
            "win_rate": wr,
            "max_drawdown": summary.get("max_drawdown"),
            "sharpe_ratio": summary.get("sharpe_ratio"),
            "sortino_ratio": summary.get("sortino_ratio"),
            "summary": summary,
        })

    ok_rows = [r for r in rows if not r.get("error")]
    if not ok_rows:
        return {"error": "Portfolio backtest produced no valid symbol runs", "portfolio": {"symbols": rows}}

    avg_win = (weighted_win / total_trades) if total_trades else 0.0
    return {
        "portfolio": True,
        "symbols_tested": len(ok_rows),
        "symbols_failed": len(rows) - len(ok_rows),
        "total_pnl": round(total_pnl, 2),
        "trade_count": total_trades,
        "win_rate": round(avg_win, 2),
        "symbol_results": rows,
        "summary": {
            "total_pnl": round(total_pnl, 2),
            "total_trades": total_trades,
            "win_rate": round(avg_win, 2),
            "symbols_tested": len(ok_rows),
        },
    }
