"""Portfolio-aware parameter sweep — rank configs by cross-symbol aggregate."""

from __future__ import annotations

from typing import Any, Callable

from app.services.bots.backtest_walk_forward import row_objective_value, row_trade_count


def aggregate_portfolio_rows(
    symbol_rows: list[dict],
    *,
    objective: str = "total_pnl",
) -> dict[str, Any]:
    """Combine per-symbol sweep rows into one portfolio row."""
    if not symbol_rows:
        return {"error": "No symbol results"}

    pnls = []
    trades = 0
    sharpes = []
    errors = []

    for row in symbol_rows:
        if row.get("error"):
            errors.append(row["error"])
            continue
        summary = row.get("summary") or {}
        pnl = row.get("total_pnl")
        if pnl is None:
            pnl = summary.get("total_pnl")
        if pnl is not None:
            pnls.append(float(pnl))
        trades += int(row.get("trade_count") or summary.get("total_trades") or 0)
        sh = summary.get("sharpe_ratio")
        if sh is not None:
            sharpes.append(float(sh))

    if errors and not pnls:
        return {"error": errors[0], "symbol_errors": errors}

    total_pnl = sum(pnls) if pnls else 0.0
    mean_sharpe = sum(sharpes) / len(sharpes) if sharpes else None
    min_pnl = min(pnls) if pnls else 0.0

    summary = {
        "total_pnl": total_pnl,
        "total_trades": trades,
        "sharpe_ratio": mean_sharpe,
        "min_symbol_pnl": min_pnl,
        "symbols_ok": len(pnls),
        "symbols_failed": len(errors),
    }

    row = {
        "summary": summary,
        "total_pnl": total_pnl,
        "trade_count": trades,
        "symbol_results": symbol_rows,
        "portfolio": True,
    }
    row["portfolio_score"] = row_objective_value(row, objective)
    if mean_sharpe is not None and objective in ("sharpe_ratio", "calmar_ratio", "robust_score"):
        row["portfolio_score"] = float(mean_sharpe)
    if objective == "total_pnl":
        row["portfolio_score"] = total_pnl
    return row


def portfolio_sweep_row(
    config: dict,
    sym_rows: list[dict],
    *,
    objective: str,
    label_fn: Callable[[dict], str],
) -> dict[str, Any]:
    agg = aggregate_portfolio_rows(sym_rows, objective=objective)
    if agg.get("error") and not agg.get("summary"):
        return {
            "label": label_fn(config),
            "config": config,
            "error": agg["error"],
            "portfolio": True,
        }
    return {
        "label": label_fn(config),
        "config": config,
        "summary": agg.get("summary") or {},
        "total_pnl": agg.get("total_pnl"),
        "trade_count": agg.get("trade_count"),
        "symbol_results": sym_rows,
        "portfolio": True,
        "portfolio_score": agg.get("portfolio_score"),
    }


def rank_portfolio_sweep_rows(
    rows: list[dict],
    *,
    objective: str,
    min_trades: int = 0,
) -> list[dict]:
    eligible = [
        r for r in rows
        if not r.get("error") and row_trade_count(r) >= max(0, int(min_trades or 0))
    ]
    return sorted(
        eligible,
        key=lambda r: float(r.get("portfolio_score") or row_objective_value(r, objective)),
        reverse=True,
    )
