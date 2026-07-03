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


# ── Model staleness & rolling accuracy tracker ─────────────────────────────

_rolling_predictions: dict[str, list[dict]] = {}  # bot_id → [{predicted: float, actual: bool, ts}]

STALENESS_MAX_AGE_HOURS = 168  # 7 days — retrain if model is older
STALENESS_MIN_ACCURACY = 0.50  # alert if rolling accuracy drops below 50%
STALENESS_WINDOW_SIZE = 30  # track last 30 predictions


def record_prediction_outcome(
    bot_id: str,
    predicted_prob: float,
    actual_win: bool,
    *,
    timestamp: float | None = None,
) -> None:
    """Record a prediction/outcome pair for rolling accuracy tracking."""
    import time

    entry = {
        "predicted": float(predicted_prob),
        "actual": bool(actual_win),
        "ts": timestamp or time.time(),
    }
    if bot_id not in _rolling_predictions:
        _rolling_predictions[bot_id] = []
    _rolling_predictions[bot_id].append(entry)
    # Keep bounded
    if len(_rolling_predictions[bot_id]) > STALENESS_WINDOW_SIZE * 2:
        _rolling_predictions[bot_id] = _rolling_predictions[bot_id][-STALENESS_WINDOW_SIZE:]


def get_model_staleness_report(bot_id: str) -> dict:
    """Check model staleness: age, rolling accuracy, retraining recommendation.

    Returns:
        {
            "bot_id": str,
            "stale": bool,
            "reasons": [str],
            "rolling_accuracy": float | None,
            "rolling_n": int,
            "model_age_hours": float | None,
            "retrain_recommended": bool,
        }
    """
    import time

    reasons: list[str] = []
    retrain = False

    # Rolling accuracy from in-memory tracker
    preds = _rolling_predictions.get(bot_id, [])
    recent = preds[-STALENESS_WINDOW_SIZE:]
    rolling_n = len(recent)
    rolling_accuracy = None
    if rolling_n >= 5:
        correct = sum(
            1 for p in recent
            if (p["predicted"] >= 0.5) == p["actual"]
        )
        rolling_accuracy = correct / rolling_n
        if rolling_accuracy < STALENESS_MIN_ACCURACY:
            reasons.append(
                f"Rolling accuracy {rolling_accuracy:.1%} is below "
                f"{STALENESS_MIN_ACCURACY:.0%} threshold ({rolling_n} samples)"
            )
            retrain = True

    # Model age check from metadata
    model_age_hours = None
    try:
        from app.services.bots.meta_label_model import get_meta_label_status

        status = get_meta_label_status(bot_id)
        meta = status.get("metadata") or {}
        trained_at = meta.get("trained_at")
        if trained_at:
            from datetime import datetime, timezone

            if isinstance(trained_at, str):
                if trained_at.endswith("Z"):
                    trained_at = trained_at[:-1] + "+00:00"
                dt = datetime.fromisoformat(trained_at)
            else:
                dt = datetime.fromtimestamp(float(trained_at), tz=timezone.utc)
            model_age_hours = (time.time() - dt.timestamp()) / 3600.0
            if model_age_hours > STALENESS_MAX_AGE_HOURS:
                reasons.append(
                    f"Model age {model_age_hours:.0f}h exceeds "
                    f"{STALENESS_MAX_AGE_HOURS}h limit"
                )
                retrain = True

        if not status.get("model_loaded"):
            reasons.append("No model loaded — training required")
            retrain = True
    except Exception:
        pass

    return {
        "bot_id": bot_id,
        "stale": bool(reasons),
        "reasons": reasons,
        "rolling_accuracy": round(rolling_accuracy, 4) if rolling_accuracy is not None else None,
        "rolling_n": rolling_n,
        "model_age_hours": round(model_age_hours, 1) if model_age_hours is not None else None,
        "retrain_recommended": retrain,
    }

