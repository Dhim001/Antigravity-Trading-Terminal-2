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
    min_wfe: float | None = None,
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
            min_wfe=min_wfe,
        )
        checks.append(_check(
            check_id="wf_oos",
            level="block" if not ok else "pass",
            ok=ok,
            message="Walk-forward OOS validation passed" if ok else reason,
            detail=None if ok else reason,
        ))
        if metrics.get("deflated_sharpe_ratio") is not None:
            dsr = float(metrics["deflated_sharpe_ratio"])
            checks.append(_check(
                check_id="wf_dsr",
                level="warn" if dsr < 0.95 else "pass",
                ok=dsr >= 0.95,
                message=(
                    f"Deflated Sharpe {dsr:.2%} (selection-bias adjusted)"
                    if dsr >= 0.95
                    else f"Deflated Sharpe {dsr:.2%} — high trial count may inflate IS Sharpe"
                ),
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

        holdout = scoped.get("final_holdout") or (scoped.get("walk_forward") or {}).get("final_holdout")
        if holdout and not holdout.get("skipped"):
            if holdout.get("error"):
                checks.append(_check(
                    check_id="final_holdout",
                    level="block",
                    ok=False,
                    message=f"Final holdout failed: {holdout['error']}",
                ))
            else:
                ho_pnl = float(holdout.get("total_pnl") or 0)
                ho_trades = int(holdout.get("trade_count") or 0)
                ho_passed = bool(holdout.get("passed", True))
                metrics["holdout_pnl"] = round(ho_pnl, 4)
                metrics["holdout_trades"] = ho_trades
                checks.append(_check(
                    check_id="final_holdout",
                    level="block" if not ho_passed else "pass",
                    ok=ho_passed,
                    message=(
                        f"Final holdout passed (${ho_pnl:.2f}, {ho_trades} trades)"
                        if ho_passed
                        else (
                            f"Final holdout failed (${ho_pnl:.2f}, {ho_trades} trades) "
                            f"— reserved segment never used in optimization"
                        )
                    ),
                ))

        pbo = scoped.get("pbo_audit") or (scoped.get("walk_forward") or {}).get("pbo_audit")
        if pbo and pbo.get("pbo") is not None:
            pbo_val = float(pbo["pbo"])
            metrics["pbo"] = pbo_val
            risk = str(pbo.get("risk_label") or "low")
            if pbo_val >= 0.5:
                checks.append(_check(
                    check_id="pbo_audit",
                    level="block",
                    ok=False,
                    message=f"PBO {pbo_val:.0%} — high overfit risk ({risk})",
                    detail="IS winner frequently underperforms on CSCV OOS splits",
                ))
            elif pbo_val >= 0.35:
                checks.append(_check(
                    check_id="pbo_audit",
                    level="warn",
                    ok=False,
                    message=f"PBO {pbo_val:.0%} — moderate overfit risk",
                ))
            else:
                checks.append(_check(
                    check_id="pbo_audit",
                    level="pass",
                    ok=True,
                    message=f"PBO {pbo_val:.0%} — low overfit risk",
                ))

        regime = (scoped.get("walk_forward") or {}).get("aggregate", {}).get("regime_analysis") or {}
        if regime.get("single_regime_risk"):
            checks.append(_check(
                check_id="wf_regime",
                level="warn",
                ok=False,
                message="OOS PnL concentrated in one vol regime",
                detail=regime.get("note") or "May not generalize across market conditions",
            ))
        elif regime.get("regime_stable") is False and len(regime.get("profitable_regimes") or []) < 2:
            checks.append(_check(
                check_id="wf_regime",
                level="warn",
                ok=False,
                message="Profitable in fewer than 2 vol regimes",
                detail="Consider regime-conditional optimization or longer history",
            ))
    elif scoped.get("sweep") and not scoped.get("walk_forward"):
        checks.append(_check(
            check_id="exploratory_sweep",
            level="block",
            ok=False,
            message="Exploratory sweep only — run walk-forward before deploy",
            detail="In-sample sweep winners are not OOS-validated",
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

    event_manifest = (scoped.get("meta") or results.get("meta") or {}).get("event_manifest") or {}
    splits = int(event_manifest.get("splits_in_range") or 0)
    price_adjust = str(event_manifest.get("price_adjust") or "raw")
    if splits > 0 and price_adjust == "raw":
        checks.append(_check(
            check_id="corp_split_adjust",
            level="warn",
            ok=False,
            message=f"Backtest spans {splits} split(s) with raw (unadjusted) prices",
            detail="Set BACKTEST_PRICE_ADJUST=split_only or total_return for accurate PnL.",
        ))

    # ── ML Strategy-Specific Checks ────────────────────────────────────────
    if run_config:
        deploy_strategy = run_config.get("strategy") or (results or {}).get("meta", {}).get("strategy") or ""
    else:
        deploy_strategy = (results or {}).get("meta", {}).get("strategy") or ""
    try:
        from app.services.bots.ml_walk_forward_validator import (
            is_ensemble_strategy,
            is_ml_strategy,
        )
        from app.services.bots.ml_retrain_scheduler import get_model_age_hours, get_model_metadata

        if is_ensemble_strategy(deploy_strategy) and symbol:
            cfg = run_config or {}
            ml_id = str(cfg.get("ml_strategy") or "ML_SIGNAL_BOOST").upper()
            rl_id = str(cfg.get("rl_strategy") or "RL_PPO_AGENT").upper()
            ta_id = str(cfg.get("ta_strategy") or "MACD_RSI").upper()
            skip_val = bool(cfg.get("ml_skip_validation_gate"))

            checks.append(_check(
                check_id="ensemble_components",
                level="pass",
                ok=True,
                message=f"Ensemble legs: TA={ta_id} · ML={ml_id} · RL={rl_id}",
            ))

            for leg_id, label in ((ml_id, "ML"), (rl_id, "RL")):
                age = get_model_age_hours(leg_id, symbol)
                if age is None:
                    checks.append(_check(
                        check_id=f"ensemble_{label.lower()}_model",
                        level="block",
                        ok=False,
                        message=f"No trained {leg_id} model for {symbol} ({label} leg)",
                        detail="Train component models in Model Training before deploying the ensemble.",
                    ))
                else:
                    checks.append(_check(
                        check_id=f"ensemble_{label.lower()}_model",
                        level="pass",
                        ok=True,
                        message=f"{leg_id} model exists ({label} leg, {age:.0f}h old)",
                    ))

            # Walk-forward / PBO gates apply to the ML classification leg
            ml_meta = get_model_metadata(ml_id, symbol) or {}
            wf_meta = ml_meta.get("walk_forward") if isinstance(ml_meta.get("walk_forward"), dict) else {}
            validated_at = ml_meta.get("validated_at") or wf_meta.get("validated_at")
            wf_ok = bool(wf_meta.get("ok"))
            recommendation = str(wf_meta.get("recommendation") or "")
            if skip_val:
                checks.append(_check(
                    check_id="ml_walk_forward",
                    level="warn",
                    ok=False,
                    message="Ensemble ML-leg WF gate skipped (ml_skip_validation_gate)",
                ))
            elif not validated_at or not wf_ok:
                checks.append(_check(
                    check_id="ml_walk_forward",
                    level="block",
                    ok=False,
                    message=f"{ml_id} (ensemble ML leg) has not passed walk-forward validation",
                    detail="Validate the ML component in Model Training before deploy.",
                ))
            elif "REJECT" in recommendation.upper():
                checks.append(_check(
                    check_id="ml_walk_forward",
                    level="block",
                    ok=False,
                    message=f"{ml_id} walk-forward recommendation is REJECT",
                    detail=recommendation[:200],
                ))
            else:
                checks.append(_check(
                    check_id="ml_walk_forward",
                    level="pass",
                    ok=True,
                    message=f"{ml_id} walk-forward validated (ensemble ML leg)",
                    detail=recommendation[:200] if recommendation else None,
                ))

            if ml_meta.get("pbo") is not None:
                pbo_val = float(ml_meta["pbo"])
                if pbo_val > 0.5:
                    checks.append(_check(
                        check_id="ml_pbo",
                        level="block",
                        ok=False,
                        message=f"Ensemble ML-leg PBO {pbo_val:.0%} — high overfitting risk",
                    ))
                elif pbo_val > 0.35:
                    checks.append(_check(
                        check_id="ml_pbo",
                        level="warn",
                        ok=False,
                        message=f"Ensemble ML-leg PBO {pbo_val:.0%} — moderate overfitting risk",
                    ))

        elif is_ml_strategy(deploy_strategy) and symbol:
            # Check 1: Model must exist
            model_age = get_model_age_hours(deploy_strategy, symbol)
            if model_age is None:
                checks.append(_check(
                    check_id="ml_model_exists",
                    level="block",
                    ok=False,
                    message=f"No trained {deploy_strategy} model for {symbol}",
                    detail="Train a model before deploying an ML strategy.",
                ))
            else:
                checks.append(_check(
                    check_id="ml_model_exists",
                    level="pass",
                    ok=True,
                    message=f"{deploy_strategy} model exists for {symbol}",
                ))

                # Check 2: Model age
                max_age = float((run_config or {}).get("ml_max_model_age_hours", 168))
                if model_age > max_age:
                    checks.append(_check(
                        check_id="ml_model_age",
                        level="warn",
                        ok=False,
                        message=f"ML model is {model_age:.0f}h old (max {max_age:.0f}h)",
                        detail="Consider retraining before deployment.",
                    ))

                meta = get_model_metadata(deploy_strategy, symbol) or {}
                skip_val = bool((run_config or {}).get("ml_skip_validation_gate"))

                # Check 3: Walk-forward validation required before live deploy
                wf_meta = meta.get("walk_forward") if isinstance(meta.get("walk_forward"), dict) else {}
                validated_at = meta.get("validated_at") or wf_meta.get("validated_at")
                wf_ok = bool(wf_meta.get("ok"))
                recommendation = str(wf_meta.get("recommendation") or "")
                if skip_val:
                    checks.append(_check(
                        check_id="ml_walk_forward",
                        level="warn",
                        ok=False,
                        message="ML walk-forward gate skipped (ml_skip_validation_gate)",
                        detail="Only use for paper/debug — philosophy requires WF before live.",
                    ))
                elif not validated_at or not wf_ok:
                    checks.append(_check(
                        check_id="ml_walk_forward",
                        level="block",
                        ok=False,
                        message="ML model has not passed walk-forward validation",
                        detail="Run Model Training → Validate (WF) before deploying.",
                    ))
                elif "REJECT" in recommendation.upper():
                    checks.append(_check(
                        check_id="ml_walk_forward",
                        level="block",
                        ok=False,
                        message="Walk-forward recommendation is REJECT",
                        detail=recommendation[:200],
                    ))
                else:
                    acc = wf_meta.get("mean_oos_accuracy")
                    acc_txt = f", mean OOS acc {float(acc):.0%}" if acc is not None else ""
                    checks.append(_check(
                        check_id="ml_walk_forward",
                        level="pass",
                        ok=True,
                        message=f"Walk-forward validated{acc_txt}",
                        detail=recommendation[:200] if recommendation else None,
                    ))

                # Check 4: PBO from model metadata
                if meta.get("pbo") is not None:
                    pbo_val = float(meta["pbo"])
                    if pbo_val > 0.5:
                        checks.append(_check(
                            check_id="ml_pbo",
                            level="block",
                            ok=False,
                            message=f"ML model PBO {pbo_val:.0%} — high overfitting risk",
                            detail="PBO > 50% indicates model likely won't generalize.",
                        ))
                    elif pbo_val > 0.35:
                        checks.append(_check(
                            check_id="ml_pbo",
                            level="warn",
                            ok=False,
                            message=f"ML model PBO {pbo_val:.0%} — moderate overfitting risk",
                        ))
                    else:
                        checks.append(_check(
                            check_id="ml_pbo",
                            level="pass",
                            ok=True,
                            message=f"ML model PBO {pbo_val:.0%} — low overfitting risk",
                        ))
                elif bool((run_config or {}).get("ml_require_pbo")):
                    checks.append(_check(
                        check_id="ml_pbo",
                        level="block",
                        ok=False,
                        message="PBO required but not present on model metadata",
                        detail="Re-run Validate with PBO enabled, or clear ml_require_pbo.",
                    ))
                else:
                    checks.append(_check(
                        check_id="ml_pbo",
                        level="warn",
                        ok=False,
                        message="No PBO audit on model metadata",
                        detail="Optional but recommended — enable PBO on Validate.",
                    ))

                # Check 5: pinned model_version resolves to a known snapshot (or current)
                pinned = (run_config or {}).get("model_version")
                if pinned:
                    from app.services.bots.ml_model_artifacts import (
                        find_version_entry,
                        model_root_for,
                    )

                    root = model_root_for(deploy_strategy, symbol)
                    entry = find_version_entry(root, str(pinned)) if root else None
                    current_at = meta.get("trained_at") if meta else None
                    if entry:
                        is_current = bool(
                            current_at
                            and entry.get("trained_at")
                            and str(entry.get("trained_at")) == str(current_at)
                        )
                        checks.append(_check(
                            check_id="ml_model_version",
                            level="pass",
                            ok=True,
                            message=(
                                f"Pinned model version resolved"
                                f"{' (current)' if is_current else ' (historical snapshot)'}"
                            ),
                            detail=f"Pin {pinned} → {entry.get('version_id')}",
                        ))
                    elif current_at and str(pinned) == str(current_at):
                        checks.append(_check(
                            check_id="ml_model_version",
                            level="pass",
                            ok=True,
                            message=f"Pinned model version matches current ({pinned})",
                        ))
                    else:
                        checks.append(_check(
                            check_id="ml_model_version",
                            level="warn",
                            ok=False,
                            message="Pinned model_version not found on disk",
                            detail=f"Pin {pinned} — activate or retrain, or clear the pin.",
                        ))
                elif (run_config or {}).get("model_artifact"):
                    checks.append(_check(
                        check_id="ml_model_version",
                        level="pass",
                        ok=True,
                        message=f"Model artifact pinned: {(run_config or {}).get('model_artifact')}",
                    ))
    except ImportError:
        pass  # ML modules not installed

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
