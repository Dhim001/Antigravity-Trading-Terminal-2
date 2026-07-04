"""Deploy gate — forward-test before capital (backtest → OOS/WF → deploy)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.services.agent.pipeline import validate_walk_forward_oos


def config_fingerprint(
    *,
    symbol: str | None = None,
    strategy: str | None = None,
    days: str | int | None = None,
    timeframe: str | None = None,
    config: dict | None = None,
) -> str:
    """Stable fingerprint for deploy parity (mirrors frontend backtestFingerprint)."""
    cfg = config or {}
    payload = {
        "symbol": symbol,
        "strategy": strategy,
        "days": str(days) if days is not None else None,
        "timeframe": timeframe,
        "allocation": cfg.get("allocation"),
        "trailing_stop_percent": cfg.get("trailing_stop_percent"),
        "take_profit_percent": cfg.get("take_profit_percent"),
        "tp_mode": cfg.get("tp_mode"),
        "min_confidence": cfg.get("min_confidence"),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _check(
    *,
    check_id: str,
    level: str,
    ok: bool,
    message: str,
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "level": level,
        "ok": ok,
        "message": message,
        "detail": detail,
    }


def _symbol_slice(results: dict, symbol: str | None) -> dict:
    """Portfolio runs: evaluate the row for the deploy symbol when possible."""
    if not results.get("portfolio") or not symbol:
        return results
    sym = str(symbol).upper()
    for row in results.get("symbol_results") or []:
        if str(row.get("symbol") or "").upper() == sym:
            if row.get("error"):
                return {**results, "_symbol_error": row.get("error")}
            return {
                **results,
                "total_pnl": row.get("total_pnl"),
                "trade_count": row.get("trade_count"),
                "summary": row.get("summary") or {},
                "walk_forward": row.get("walk_forward"),
                "_portfolio_symbol": sym,
            }
    return results


def evaluate_deploy_gate(
    results: dict | None,
    *,
    symbol: str | None = None,
    deploy_fingerprint: str | None = None,
    run_config: dict | None = None,
    run_days: str | int | None = None,
    run_timeframe: str | None = None,
    min_trades: int = 1,
    min_pnl: float = 0.0,
    min_stability_score: float = 0.5,
    max_drawdown_warn_pct: float = 25.0,
) -> dict[str, Any]:
    """Evaluate whether a backtest run satisfies deploy prerequisites."""
    checks: list[dict[str, Any]] = []

    if not results:
        checks.append(_check(
            check_id="backtest_linked",
            level="warn",
            ok=False,
            message="No backtest run linked",
            detail="Run a backtest and deploy from results, or use force deploy.",
        ))
        return {
            "passed": True,
            "blocking": False,
            "checks": checks,
            "workflow_stage": "backtest",
            "block_reason": None,
            "metrics": {},
        }

    scoped = _symbol_slice(results, symbol)
    sym_err = scoped.pop("_symbol_error", None)
    if sym_err:
        checks.append(_check(
            check_id="symbol_backtest",
            level="block",
            ok=False,
            message=f"Portfolio backtest failed for {symbol}",
            detail=str(sym_err),
        ))
        return _finalize(checks, workflow_stage="blocked")

    metrics: dict[str, Any] = {}

    if scoped.get("walk_forward"):
        ok, reason, metrics = validate_walk_forward_oos(
            scoped,
            min_oos_pnl=min_pnl,
            min_oos_trades=min_trades,
            min_stability_score=min_stability_score,
        )
        checks.append(_check(
            check_id="wf_oos",
            level="block" if not ok else "pass",
            ok=ok,
            message="Walk-forward OOS validation passed" if ok else reason,
            detail=None if ok else reason,
        ))
        if metrics.get("stability_score") is not None and int(metrics.get("fold_count") or 0) >= 3:
            stab = float(metrics["stability_score"])
            stab_ok = stab >= min_stability_score
            checks.append(_check(
                check_id="wf_stability",
                level="block" if not stab_ok else "pass",
                ok=stab_ok,
                message=(
                    f"OOS stability {stab:.0%} across {metrics['fold_count']} folds"
                    if stab_ok
                    else f"OOS stability {stab:.0%} below {min_stability_score:.0%}"
                ),
            ))
    else:
        summary = scoped.get("summary") or {}
        pnl = scoped.get("total_pnl")
        if pnl is None:
            pnl = summary.get("total_pnl")
        try:
            pnl_f = float(pnl or 0)
        except (TypeError, ValueError):
            pnl_f = 0.0

        trades = scoped.get("trade_count")
        if trades is None:
            trades = summary.get("total_trades")
        try:
            trades_i = int(trades or 0)
        except (TypeError, ValueError):
            trades_i = 0

        meta = scoped.get("meta") or {}
        oos_note = None
        if meta.get("oos_pct"):
            oos_note = f"Results use {meta['oos_pct']}% OOS holdout window"

        trades_ok = trades_i >= max(0, int(min_trades))
        checks.append(_check(
            check_id="trade_count",
            level="block" if not trades_ok else "pass",
            ok=trades_ok,
            message=(
                f"{trades_i} trades (minimum {min_trades})"
                if trades_ok
                else f"Only {trades_i} trades — need at least {min_trades}"
            ),
            detail=oos_note,
        ))

        pnl_ok = pnl_f >= float(min_pnl)
        checks.append(_check(
            check_id="pnl",
            level="block" if not pnl_ok else "pass",
            ok=pnl_ok,
            message=(
                f"PnL ${pnl_f:.2f} meets minimum ${min_pnl:.2f}"
                if pnl_ok
                else f"PnL ${pnl_f:.2f} below minimum ${min_pnl:.2f}"
            ),
            detail=oos_note,
        ))
        metrics = {"pnl": round(pnl_f, 4), "trades": trades_i}

        max_dd = summary.get("max_drawdown_pct")
        if max_dd is not None:
            try:
                dd_f = float(max_dd)
                if dd_f > max_drawdown_warn_pct:
                    checks.append(_check(
                        check_id="max_drawdown",
                        level="warn",
                        ok=False,
                        message=f"Max drawdown {dd_f:.1f}% exceeds {max_drawdown_warn_pct:.0f}% guideline",
                    ))
            except (TypeError, ValueError):
                pass

    if scoped.get("portfolio") and not scoped.get("_portfolio_symbol") and symbol:
        checks.append(_check(
            check_id="portfolio_symbol",
            level="warn",
            ok=False,
            message=f"No per-symbol result for {symbol} in portfolio run",
            detail="Gate used aggregate portfolio metrics.",
        ))

    corr = scoped.get("correlation_summary") or results.get("correlation_summary")
    if corr and corr.get("warning"):
        checks.append(_check(
            check_id="correlation",
            level="warn",
            ok=False,
            message="High portfolio correlation",
            detail=corr.get("message"),
        ))

    if deploy_fingerprint and run_config is not None:
        run_fp = config_fingerprint(
            symbol=symbol or results.get("meta", {}).get("symbol"),
            strategy=results.get("meta", {}).get("strategy"),
            days=run_days,
            timeframe=run_timeframe,
            config=run_config,
        )
        if run_fp != deploy_fingerprint:
            checks.append(_check(
                check_id="config_fingerprint",
                level="warn",
                ok=False,
                message="Deploy config differs from backtest snapshot",
                detail="Strategy parameters changed since the linked backtest run.",
            ))

    stage = "ready"
    if any(c["level"] == "block" and not c["ok"] for c in checks):
        stage = "blocked"
    elif any(c["id"] == "backtest_linked" for c in checks):
        stage = "backtest"
    elif scoped.get("walk_forward"):
        stage = "oos_validated" if stage == "ready" else stage

    return _finalize(checks, workflow_stage=stage, metrics=metrics)


def _finalize(
    checks: list[dict[str, Any]],
    *,
    workflow_stage: str,
    metrics: dict | None = None,
) -> dict[str, Any]:
    blocking = any(c["level"] == "block" and not c["ok"] for c in checks)
    passed = not blocking
    block_reason = None
    if blocking:
        failed = [c for c in checks if c["level"] == "block" and not c["ok"]]
        block_reason = failed[0]["message"] if failed else "Deploy gate blocked"
    return {
        "passed": passed,
        "blocking": blocking,
        "checks": checks,
        "workflow_stage": workflow_stage,
        "block_reason": block_reason,
        "metrics": metrics or {},
    }


def enrich_deploy_config(
    config: dict | None,
    *,
    run_id: str | None = None,
    fingerprint: str | None = None,
    gate: dict | None = None,
) -> dict:
    """Persist deploy audit fields on the bot config."""
    out = dict(config or {})
    if run_id:
        out["backtest_run_id"] = run_id
    if fingerprint:
        out["backtest_fingerprint"] = fingerprint
    if gate and gate.get("passed"):
        out["deploy_gate_passed_at"] = datetime.now(timezone.utc).isoformat()
    out.setdefault("deploy_workflow", "backtest→oos→deploy")
    return out
