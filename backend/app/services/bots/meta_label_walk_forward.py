"""Walk-forward evaluation — train GBM meta-label on IS window, validate on OOS."""

from __future__ import annotations

import copy
import uuid
from typing import Any, Callable

from app.config import META_LABEL_MIN_TRAIN_SAMPLES
from app.services.bots.backtest_walk_forward import build_rolling_fold_windows
from app.services.bots.meta_label_model import (
    clear_backtest_session_model,
    insight_to_features,
    set_backtest_session_model,
    train_model_from_rows,
)
from app.services.market.timeframes import normalize_timeframe


def closed_rows_from_backtest_trades(
    trades: list[dict],
    *,
    symbol: str,
    timeframe: str = "1m",
) -> list[dict[str, Any]]:
    """Pair backtest trade_log entries into meta-label training rows."""
    sym = str(symbol or "").upper()
    tf = normalize_timeframe(timeframe or "1m")
    pending: dict[str, dict] = {}
    rows: list[dict[str, Any]] = []

    for trade in sorted(trades, key=lambda t: int(t.get("time") or 0)):
        side = str(trade.get("side") or "").upper()
        is_exit = bool(trade.get("is_exit"))

        if not is_exit:
            if side in ("BUY", "SELL"):
                pending[side] = trade
            continue

        entry_side = "BUY" if side == "SELL" else "SELL"
        entry = pending.pop(entry_side, None)
        if entry is None:
            continue
        pnl = trade.get("pnl")
        if pnl is None:
            continue
        try:
            pnl_f = float(pnl)
        except (TypeError, ValueError):
            continue

        snap = entry.get("insight_snapshot")
        if not isinstance(snap, dict):
            continue

        entry_ts = str(entry.get("time") or "")
        feat = insight_to_features(
            snap,
            symbol=sym,
            side=entry_side,
            timeframe=tf,
            entry_ts=entry_ts,
        )
        rows.append({
            "features": feat,
            "win": pnl_f > 0,
            "pnl": round(pnl_f, 4),
            "entry_ts": entry_ts,
        })

    return rows


def _summary_metrics(result: dict | None) -> dict[str, Any]:
    if not result or result.get("error"):
        return {
            "error": result.get("error") if result else "no result",
            "total_pnl": 0.0,
            "win_rate": 0.0,
            "total_trades": 0,
            "max_drawdown": 0.0,
            "expectancy": 0.0,
            "profit_factor": None,
            "filter_rejects": {},
        }
    summary = result.get("summary") or {}
    return {
        "total_pnl": summary.get("total_pnl", result.get("total_pnl")),
        "win_rate": summary.get("win_rate", result.get("win_rate")),
        "total_trades": summary.get("total_trades", result.get("trade_count")),
        "max_drawdown": summary.get("max_drawdown", result.get("max_drawdown")),
        "expectancy": summary.get("expectancy"),
        "profit_factor": summary.get("profit_factor"),
        "return_pct": summary.get("return_pct"),
        "sharpe_ratio": summary.get("sharpe_ratio"),
        "filter_rejects": summary.get("filter_rejects") or {},
        "blocked_entries": summary.get("blocked_entries", 0),
    }


def _delta(baseline: dict, variant: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("total_pnl", "win_rate", "total_trades", "max_drawdown", "expectancy"):
        b = baseline.get(key)
        v = variant.get(key)
        if b is None or v is None:
            continue
        try:
            out[key] = round(float(v) - float(b), 4)
        except (TypeError, ValueError):
            pass
    return out


def evaluate_meta_label_walk_forward(
    run_backtest: Callable[..., dict],
    symbol: str,
    strategy: str,
    config: dict,
    candles: list[dict],
    *,
    meta: dict | None = None,
    rolling_folds: int = 2,
    train_pct: float = 70.0,
    min_train_samples: int | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """
    Walk-forward compare: baseline (no gate) vs GBM meta-label gate on OOS windows.

    For each fold:
      1. Run IS backtest without gate → train GBM on closed trades with snapshots
      2. Run OOS backtest baseline (no gate)
      3. Run OOS backtest with GBM gate using in-memory model
    """
    cfg = dict(config or {})
    min_train = int(
        min_train_samples
        if min_train_samples is not None
        else cfg.get("meta_label_min_train_samples") or META_LABEL_MIN_TRAIN_SAMPLES
    )
    min_train = max(10, min_train)
    timeframe = normalize_timeframe(cfg.get("timeframe") or "1m")
    sym = str(symbol or "").upper()

    windows = build_rolling_fold_windows(candles, meta or {}, rolling_folds, train_pct)
    if not windows:
        train, test, train_meta, test_meta = _single_window(candles, meta or {}, train_pct)
        if train and test:
            windows = [(train, test, train_meta, test_meta)]
        else:
            return {
                "ok": False,
                "error": "Not enough candles for walk-forward (need ≥100 bars per fold)",
                "folds": [],
            }

    fold_results: list[dict[str, Any]] = []
    session_root = str(cfg.get("backtest_bot_id") or "wf-meta-label")

    for idx, (train_candles, test_candles, train_meta, test_meta) in enumerate(windows):
        if cancel_cb and cancel_cb():
            return {
                "ok": False,
                "error": "cancelled",
                "folds": fold_results,
            }

        fold_id = train_meta.get("fold", len(fold_results) + 1)
        if progress_cb:
            progress_cb(
                idx + 1,
                len(windows),
                f"Meta-label walk-forward fold {idx + 1}/{len(windows)}: in-sample replay…",
            )
        session_id = f"{session_root}:fold{fold_id}:{uuid.uuid4().hex[:8]}"

        train_cfg = copy.deepcopy(cfg)
        train_cfg["calibration_gate_enabled"] = False
        train_cfg.pop("meta_label_walk_forward", None)

        train_result = run_backtest(sym, strategy, train_cfg, train_candles)
        train_rows = closed_rows_from_backtest_trades(
            train_result.get("trades") or [],
            symbol=sym,
            timeframe=timeframe,
        )
        trained = train_model_from_rows(train_rows, min_samples=min_train)

        if progress_cb:
            progress_cb(
                idx + 1,
                len(windows),
                f"Meta-label walk-forward fold {idx + 1}/{len(windows)}: OOS baseline…",
            )

        baseline_cfg = copy.deepcopy(cfg)
        baseline_cfg["calibration_gate_enabled"] = False
        baseline_cfg.pop("meta_label_walk_forward", None)
        baseline = run_backtest(sym, strategy, baseline_cfg, test_candles)
        baseline_m = _summary_metrics(baseline)

        gbm_m: dict[str, Any] | None = None
        gbm_error: str | None = None
        if trained.get("ok"):
            set_backtest_session_model(session_id, trained["model"], trained)
            gbm_cfg = copy.deepcopy(cfg)
            gbm_cfg["calibration_gate_enabled"] = True
            gbm_cfg["meta_label_model_mode"] = "hybrid"
            gbm_cfg["meta_label_shadow_mode"] = False
            gbm_cfg["backtest_bot_id"] = session_id
            gbm_cfg.pop("meta_label_walk_forward", None)
            if progress_cb:
                progress_cb(
                    idx + 1,
                    len(windows),
                    f"Meta-label walk-forward fold {idx + 1}/{len(windows)}: OOS with GBM gate…",
                )
            try:
                gbm_result = run_backtest(sym, strategy, gbm_cfg, test_candles)
                gbm_m = _summary_metrics(gbm_result)
            finally:
                clear_backtest_session_model(session_id)
        else:
            gbm_error = trained.get("error") or "train failed"

        fold_results.append({
            "fold": fold_id,
            "train_bars": len(train_candles),
            "test_bars": len(test_candles),
            "train_trades": len(train_rows),
            "train_val_auc": (trained.get("metrics") or {}).get("val_auc"),
            "train_error": None if trained.get("ok") else trained.get("error"),
            "baseline_oos": baseline_m,
            "gbm_oos": gbm_m,
            "gbm_error": gbm_error,
            "gbm_vs_baseline": _delta(baseline_m, gbm_m) if gbm_m else None,
        })

    return _aggregate_fold_results(fold_results, min_train_samples=min_train)


def _single_window(
    candles: list[dict],
    meta: dict,
    train_pct: float,
) -> tuple[list[dict], list[dict], dict, dict]:
    if len(candles) < 100:
        return [], [], {}, {}
    pct = max(50.0, min(90.0, float(train_pct)))
    split = int(len(candles) * pct / 100.0)
    split = max(50, min(split, len(candles) - 50))
    train = candles[:split]
    test = candles[split:]
    train_meta = {**(meta or {}), "window": "in_sample", "count": len(train)}
    test_meta = {**(meta or {}), "window": "out_of_sample", "count": len(test)}
    return train, test, train_meta, test_meta


def _aggregate_fold_results(
    folds: list[dict[str, Any]],
    *,
    min_train_samples: int,
) -> dict[str, Any]:
    if not folds:
        return {"ok": False, "error": "no folds completed", "folds": []}

    trained_folds = [f for f in folds if f.get("gbm_oos")]
    if not trained_folds:
        return {
            "ok": False,
            "error": "GBM did not train on any fold (need more closed trades with insight snapshots)",
            "folds": folds,
            "min_train_samples": min_train_samples,
        }

    def _avg(key: str, section: str) -> float | None:
        vals = []
        for f in trained_folds:
            block = f.get(section) or {}
            v = block.get(key)
            if v is not None:
                try:
                    vals.append(float(v))
                except (TypeError, ValueError):
                    pass
        if not vals:
            return None
        return round(sum(vals) / len(vals), 4)

    baseline_avg = {
        "total_pnl": _avg("total_pnl", "baseline_oos"),
        "win_rate": _avg("win_rate", "baseline_oos"),
        "total_trades": _avg("total_trades", "baseline_oos"),
        "expectancy": _avg("expectancy", "baseline_oos"),
    }
    gbm_avg = {
        "total_pnl": _avg("total_pnl", "gbm_oos"),
        "win_rate": _avg("win_rate", "gbm_oos"),
        "total_trades": _avg("total_trades", "gbm_oos"),
        "expectancy": _avg("expectancy", "gbm_oos"),
    }
    delta = _delta(baseline_avg, gbm_avg)

    gbm_better = (
        (delta.get("total_pnl") or 0) > 0
        or ((delta.get("expectancy") or 0) > 0 and (delta.get("total_trades") or 0) <= 0)
    )
    recommendation = (
        "GBM gate improved OOS expectancy or PnL — consider hybrid mode with shadow off."
        if gbm_better
        else "GBM gate did not beat baseline on OOS — keep shadow mode or stay on Wilson."
    )

    return {
        "ok": True,
        "folds": folds,
        "folds_evaluated": len(trained_folds),
        "min_train_samples": min_train_samples,
        "aggregate": {
            "baseline_oos_avg": baseline_avg,
            "gbm_oos_avg": gbm_avg,
            "gbm_vs_baseline_avg": delta,
        },
        "recommendation": recommendation,
    }
