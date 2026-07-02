"""Operational rollout helpers for GBM meta-label gate (shadow → live)."""

from __future__ import annotations

from typing import Any

from app.services.bots.indicators import merge_strategy_config
from app.services.bots.meta_label_walk_forward import evaluate_meta_label_walk_forward
from app.services.bots.risk_sizing import enrich_backtest_risk_config

OPERATIONAL_STAGES = frozenset({"shadow", "promote", "rollback"})


def build_operational_patch(
    stage: str,
    *,
    walk_forward: dict[str, Any] | None = None,
    require_positive_oos: bool = True,
) -> dict[str, Any]:
    """Config patch for shadow evaluation, live promotion, or rollback."""
    key = str(stage or "").strip().lower()
    if key not in OPERATIONAL_STAGES:
        raise ValueError(f"stage must be one of: {', '.join(sorted(OPERATIONAL_STAGES))}")

    if key == "shadow":
        return {
            "calibration_gate_enabled": True,
            "meta_label_model_mode": "hybrid",
            "meta_label_shadow_mode": True,
        }

    if key == "rollback":
        return {
            "calibration_gate_enabled": False,
            "meta_label_shadow_mode": False,
            "meta_label_model_mode": "wilson",
        }

    # promote
    if require_positive_oos:
        _assert_walk_forward_improved(walk_forward)

    return {
        "calibration_gate_enabled": True,
        "meta_label_model_mode": "hybrid",
        "meta_label_shadow_mode": False,
    }


def _assert_walk_forward_improved(walk_forward: dict[str, Any] | None) -> None:
    if not walk_forward or not walk_forward.get("ok"):
        err = (walk_forward or {}).get("error") or "walk-forward evaluation missing or failed"
        raise ValueError(
            f"Cannot promote without positive OOS walk-forward: {err}. "
            "Run POST /api/v1/backtest/meta-label-walk-forward first or pass require_positive_oos=false."
        )

    agg = walk_forward.get("aggregate") or {}
    delta = agg.get("gbm_vs_baseline_avg") or {}
    pnl_delta = float(delta.get("total_pnl") or 0)
    exp_delta = float(delta.get("expectancy") or 0)
    trades_delta = float(delta.get("total_trades") or 0)

    improved = pnl_delta > 0 or (exp_delta > 0 and trades_delta <= 0)
    if not improved:
        raise ValueError(
            "Walk-forward OOS did not beat baseline — keep shadow mode or retrain with more trades."
        )


def operational_status(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Summarize current meta-label operational stage from bot config."""
    c = cfg or {}
    gate = bool(c.get("calibration_gate_enabled"))
    shadow = bool(c.get("meta_label_shadow_mode"))
    mode = str(c.get("meta_label_model_mode") or "wilson").lower()

    if not gate:
        stage = "off"
    elif shadow:
        stage = "shadow"
    elif mode in ("gbm", "hybrid"):
        stage = "live"
    else:
        stage = "wilson_gate"

    return {
        "stage": stage,
        "calibration_gate_enabled": gate,
        "meta_label_shadow_mode": shadow,
        "meta_label_model_mode": mode,
    }


def run_meta_label_walk_forward_sync(
    run_backtest,
    feed,
    *,
    symbol: str,
    strategy: str,
    config: dict[str, Any] | None,
    days: int,
    timeframe: str | None = None,
    interval: str | None = None,
    rolling_folds: int = 2,
    train_pct: float = 70.0,
    min_train_samples: int | None = None,
    account_balance: float | None = None,
) -> dict[str, Any]:
    """Resolve candles and run meta-label walk-forward only (no full backtest)."""
    from app.services.archive.resolve import resolve_backtest_candles

    strat_key = str(strategy or "").upper()
    if strat_key != "CHART_AGENT":
        return {"ok": False, "error": "meta-label walk-forward requires CHART_AGENT strategy"}

    if not run_backtest or not feed:
        return {"ok": False, "error": "Backtester or market feed not available"}

    cfg = merge_strategy_config(strat_key, dict(config or {}))
    cfg = enrich_backtest_risk_config(cfg, account_balance)

    days = max(7, min(int(days or 30), 90))
    rolling_folds = max(1, min(int(rolling_folds or 2), 5))
    train_pct = max(50.0, min(90.0, float(train_pct or 70.0)))

    candles, meta = resolve_backtest_candles(
        str(symbol).upper(),
        feed,
        days=days,
        interval=interval,
        timeframe=timeframe or cfg.get("timeframe"),
    )
    if len(candles) < 100:
        return {
            "ok": False,
            "error": f"Not enough candles ({len(candles)}); need at least 100 bars",
            "meta": meta,
        }

    meta = dict(meta or {})
    meta["strategy"] = strat_key
    meta["symbol"] = str(symbol).upper()
    meta["days"] = days
    meta["rolling_folds"] = rolling_folds
    meta["train_pct"] = train_pct

    result = evaluate_meta_label_walk_forward(
        run_backtest,
        str(symbol).upper(),
        strat_key,
        cfg,
        candles,
        meta=meta,
        rolling_folds=rolling_folds,
        train_pct=train_pct,
        min_train_samples=min_train_samples,
    )
    result["meta"] = meta
    return result
