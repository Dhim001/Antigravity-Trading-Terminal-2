"""ML Walk-Forward Validation Orchestrator.

Runs walk-forward retraining for any ML strategy: partitions data into
rolling or anchored windows, trains on each IS fold with purged embargo,
evaluates OOS, and aggregates metrics.  This is the core anti-overfitting
engine for all ML signal strategies.

References
----------
- López de Prado, *Advances in Financial Machine Learning* (2018), Ch. 7–12
- Existing backtest_purged_cv.py for purge/embargo helpers
"""

from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from app.services.bots.backtest_purged_cv import (
    embargo_bars_for_segment,
    estimate_purge_bars,
    partition_candles,
    purge_train_before_test,
)
from app.services.bots.ml_triple_barrier import label_triple_barrier

logger = logging.getLogger(__name__)

# ── Strategy trainer dispatch ─────────────────────────────────────────────

_TRAINER_REGISTRY: dict[str, Callable] = {}


def _lazy_register():
    """Lazy import to avoid circular deps and import errors when torch missing."""
    if _TRAINER_REGISTRY:
        return
    try:
        from app.services.bots.strategies_ml import train_ml_signal_model
        _TRAINER_REGISTRY["ML_SIGNAL_BOOST"] = train_ml_signal_model
    except ImportError:
        pass
    try:
        from app.services.bots.ml_lstm_trainer import train_lstm_signal_model
        _TRAINER_REGISTRY["LSTM_DIRECTION"] = train_lstm_signal_model
    except ImportError:
        pass
    try:
        from app.services.bots.rl_ppo_trainer import train_ppo_agent
        _TRAINER_REGISTRY["RL_PPO_AGENT"] = train_ppo_agent
    except ImportError:
        pass
    try:
        from app.services.bots.ml_tcn_trainer import train_tcn_model
        _TRAINER_REGISTRY["TCN_MULTI_HORIZON"] = train_tcn_model
    except ImportError:
        pass
    try:
        from app.services.bots.ml_vae_regime import train_vae_regime_model
        _TRAINER_REGISTRY["VAE_REGIME_DETECTOR"] = train_vae_regime_model
    except ImportError:
        pass
    try:
        from app.services.bots.ml_transformer_trainer import train_transformer_model
        _TRAINER_REGISTRY["TRANSFORMER_SIGNAL"] = train_transformer_model
    except ImportError:
        pass
    try:
        from app.services.bots.ml_gnn_trainer import train_gnn_model
        _TRAINER_REGISTRY["GNN_CROSS_ASSET"] = train_gnn_model
    except ImportError:
        pass


def get_trainer(strategy: str) -> Callable | None:
    """Get the trainer function for a strategy."""
    _lazy_register()
    return _TRAINER_REGISTRY.get(strategy.upper())


ML_STRATEGIES = frozenset({
    "ML_SIGNAL_BOOST", "LSTM_DIRECTION", "RL_PPO_AGENT",
    "TCN_MULTI_HORIZON", "VAE_REGIME_DETECTOR",
    "TRANSFORMER_SIGNAL", "GNN_CROSS_ASSET",
})


def is_ml_strategy(strategy: str) -> bool:
    return str(strategy).upper() in ML_STRATEGIES


# ── Walk-forward fold generation ──────────────────────────────────────────


def generate_wf_folds(
    n_candles: int,
    *,
    n_folds: int = 5,
    mode: str = "rolling",
    purge_bars: int = 30,
    embargo_pct: float = 1.0,
    min_train: int = 200,
    min_test: int = 100,
) -> list[dict]:
    """Generate walk-forward fold indices.

    Parameters
    ----------
    n_candles : int
        Total number of candles available.
    n_folds : int
        Number of test folds (default 5).
    mode : str
        'rolling' (sliding window) or 'anchored' (expanding window).
    purge_bars : int
        Number of bars to remove between train and test.
    embargo_pct : float
        Embargo percentage after test segment.
    min_train, min_test : int
        Minimum bars required for train and test segments.

    Returns
    -------
    List of fold dicts with train_start, train_end, test_start, test_end indices.
    """
    if n_candles < min_train + min_test + purge_bars:
        return []

    n_folds = max(2, min(n_folds, 20))
    test_size = max(min_test, n_candles // (n_folds + 1))
    folds = []

    for i in range(n_folds):
        test_start = n_candles - (n_folds - i) * test_size
        test_end = test_start + test_size

        if mode == "anchored":
            train_start = 0
        else:
            # Rolling: train window is proportional, min 2× test size
            train_size = max(min_train, test_size * 3)
            train_start = max(0, test_start - purge_bars - train_size)

        train_end = test_start - purge_bars
        embargo = embargo_bars_for_segment(test_size, embargo_pct)

        if train_end - train_start < min_train:
            continue
        if test_end > n_candles:
            test_end = n_candles
        if test_end - test_start < min_test:
            continue

        folds.append({
            "fold": i,
            "train_start": train_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
            "purge_bars": purge_bars,
            "embargo_bars": embargo,
        })

    return folds


# ── OOS evaluation ────────────────────────────────────────────────────────


def evaluate_oos_accuracy(
    strategy_cls,
    test_candles: list[dict],
    config: dict,
) -> dict[str, Any]:
    """Run a trained strategy over OOS candles and compute metrics.

    Returns dict with: accuracy, n_signals, buy_count, sell_count, none_count.
    """
    key = str(strategy_cls or "").upper()
    # Fast path: batch predict for XGB signal model (avoids per-bar strategy overhead).
    if key == "ML_SIGNAL_BOOST":
        try:
            return _evaluate_oos_ml_signal_batch(test_candles, config or {})
        except Exception as exc:
            logger.warning("Batch OOS eval failed, falling back to strategy loop: %s", exc)

    from app.services.bots.strategies import get_strategy

    strat = get_strategy(strategy_cls, config)
    labels = label_triple_barrier(
        test_candles,
        atr_mult_upper=float(config.get("triple_barrier_atr_mult", 2.0)),
        atr_mult_lower=float(config.get("triple_barrier_atr_mult", 2.0)),
        max_holding_bars=int(config.get("triple_barrier_max_bars", 30)),
    )

    correct = 0
    total = 0
    counts = {"BUY": 0, "SELL": 0, "NONE": 0}

    # Stride long OOS windows in WF/PBO to keep validation responsive.
    stride = 1
    if bool((config or {}).get("_wf_mode")) and len(test_candles) > 400:
        stride = max(1, len(test_candles) // 400)

    for i in range(0, len(test_candles), stride):
        candle = test_candles[i]
        result = strat.evaluate(candle)
        signal = result.get("signal", "NONE")
        counts[signal] = counts.get(signal, 0) + 1

        if signal == "NONE":
            continue

        if i < len(labels):
            lbl = labels[i]
            actual = lbl.get("label", 0)
            if (signal == "BUY" and actual == 1) or (signal == "SELL" and actual == -1):
                correct += 1
            total += 1

    accuracy = correct / total if total > 0 else 0.0

    return {
        "accuracy": round(accuracy, 4),
        "n_signals": total,
        "n_correct": correct,
        "buy_count": counts.get("BUY", 0),
        "sell_count": counts.get("SELL", 0),
        "none_count": counts.get("NONE", 0),
        "total_bars": len(test_candles),
    }


def _evaluate_oos_ml_signal_batch(test_candles: list[dict], config: dict) -> dict[str, Any]:
    """Vectorized OOS accuracy for ML_SIGNAL_BOOST using the on-disk model."""
    from app.services.bots.ml_feature_engineering import bar_to_signal_features
    from app.services.bots.strategies_ml import get_ml_signal_store

    symbol = str(config.get("model_symbol") or config.get("symbol") or "").upper()
    if not symbol:
        raise ValueError("symbol required for batch OOS")

    store = get_ml_signal_store()
    threshold = float(config.get("min_confidence", 0.55))
    lookback_size = 20
    labels = label_triple_barrier(
        test_candles,
        atr_mult_upper=float(config.get("triple_barrier_atr_mult", 2.0)),
        atr_mult_lower=float(config.get("triple_barrier_atr_mult", 2.0)),
        max_holding_bars=int(config.get("triple_barrier_max_bars", 30)),
    )

    correct = 0
    total = 0
    counts = {"BUY": 0, "SELL": 0, "NONE": 0}

    for i, candle in enumerate(test_candles):
        if i < lookback_size:
            counts["NONE"] += 1
            continue
        lookback = test_candles[max(0, i - lookback_size):i]
        features = bar_to_signal_features(candle, lookback_rows=lookback)
        pred = store.predict(symbol, features, model_version=config.get("model_version") or None)
        if pred is None:
            counts["NONE"] += 1
            continue
        signal, confidence = pred
        if signal not in ("BUY", "SELL") or float(confidence) < threshold:
            counts["NONE"] += 1
            continue
        counts[signal] = counts.get(signal, 0) + 1
        if i < len(labels):
            actual = labels[i].get("label", 0)
            if (signal == "BUY" and actual == 1) or (signal == "SELL" and actual == -1):
                correct += 1
            total += 1

    accuracy = correct / total if total > 0 else 0.0
    return {
        "accuracy": round(accuracy, 4),
        "n_signals": total,
        "n_correct": correct,
        "buy_count": counts.get("BUY", 0),
        "sell_count": counts.get("SELL", 0),
        "none_count": counts.get("NONE", 0),
        "total_bars": len(test_candles),
    }


# ── Main walk-forward runner ──────────────────────────────────────────────


def walk_forward_ml_train(
    strategy: str,
    symbol: str,
    candles: list[dict],
    *,
    config: dict | None = None,
    n_folds: int = 5,
    mode: str = "rolling",
    embargo_pct: float = 1.0,
) -> dict[str, Any]:
    """Run walk-forward training and validation for an ML strategy.

    Parameters
    ----------
    strategy : str
        Strategy ID (e.g. 'ML_SIGNAL_BOOST').
    symbol : str
        Trading symbol.
    candles : list[dict]
        Full historical candle dataset.
    config : dict, optional
        Strategy configuration overrides.
    n_folds : int
        Number of walk-forward folds.
    mode : str
        'rolling' or 'anchored'.
    embargo_pct : float
        Embargo percentage between folds.

    Returns
    -------
    dict with:
        ok: bool
        folds: list of per-fold results
        aggregate: aggregated OOS metrics
        stability: stability analysis
        recommendation: deployment recommendation string
    """
    cfg = dict(config or {})
    cfg.setdefault("symbol", symbol)
    cfg.setdefault("model_symbol", symbol)
    # Fast training path for interactive Model Training panel validation.
    cfg.setdefault("_wf_mode", True)
    trainer = get_trainer(strategy)
    if trainer is None:
        return {
            "ok": False,
            "error": f"No trainer registered for {strategy}",
            "strategy": strategy,
            "symbol": symbol,
        }

    purge_bars = estimate_purge_bars(cfg)
    n = len(candles)
    folds = generate_wf_folds(
        n, n_folds=n_folds, mode=mode,
        purge_bars=purge_bars, embargo_pct=embargo_pct,
    )

    if not folds:
        return {
            "ok": False,
            "error": f"Insufficient data for {n_folds}-fold WF ({n} candles)",
            "strategy": strategy,
            "symbol": symbol,
        }

    fold_results = []
    for fold in folds:
        train_candles = candles[fold["train_start"]:fold["train_end"]]
        test_candles = candles[fold["test_start"]:fold["test_end"]]
        for row in train_candles:
            if isinstance(row, dict):
                row.setdefault("_symbol", symbol)
        for row in test_candles:
            if isinstance(row, dict):
                row.setdefault("_symbol", symbol)

        # Purge overlap
        train_candles, purge_info = purge_train_before_test(
            train_candles, test_candles, fold["purge_bars"],
        )

        # Train on IS fold
        try:
            train_result = trainer(symbol, train_candles, config=cfg)
        except Exception as exc:
            logger.warning("WF fold %d train failed: %s", fold["fold"], exc)
            fold_results.append({
                "fold": fold["fold"],
                "ok": False,
                "error": str(exc),
                "train_bars": len(train_candles),
                "test_bars": len(test_candles),
            })
            continue

        if not train_result.get("ok", False):
            fold_results.append({
                "fold": fold["fold"],
                "ok": False,
                "error": train_result.get("error", "Training failed"),
                "train_bars": len(train_candles),
                "test_bars": len(test_candles),
            })
            continue

        # Evaluate on OOS fold (must not abort the whole WF run)
        try:
            oos_metrics = evaluate_oos_accuracy(strategy, test_candles, cfg)
        except Exception as exc:
            logger.warning("WF fold %d OOS eval failed: %s", fold["fold"], exc)
            fold_results.append({
                "fold": fold["fold"],
                "ok": False,
                "error": f"OOS eval failed: {exc}",
                "train_bars": len(train_candles),
                "test_bars": len(test_candles),
                "train_metrics": train_result.get("metrics", {}),
                "purge": purge_info,
            })
            continue

        train_metrics = train_result.get("metrics", {})
        if isinstance(train_metrics, dict) and str(strategy).upper() == "RL_PPO_AGENT":
            # Keep wire payload small / JSON-safe (drop per-episode histories).
            train_metrics = {
                k: train_metrics.get(k)
                for k in (
                    "total_timesteps",
                    "episodes",
                    "mean_return_pct",
                    "best_mean_return",
                    "mean_trades_per_episode",
                    "hidden_dim",
                )
                if train_metrics.get(k) is not None
            }

        fold_results.append({
            "fold": fold["fold"],
            "ok": True,
            "train_bars": len(train_candles),
            "test_bars": len(test_candles),
            "accuracy": oos_metrics.get("accuracy"),
            "n_samples": oos_metrics.get("n_signals"),
            "train_metrics": train_metrics,
            "oos_metrics": oos_metrics,
            "purge": purge_info,
        })

    # Aggregate results
    successful = [f for f in fold_results if f.get("ok")]
    if not successful:
        fold_errs = [f.get("error") for f in fold_results if f.get("error")]
        detail = fold_errs[0] if fold_errs else "unknown fold errors"
        return {
            "ok": False,
            "error": f"All folds failed — {detail}",
            "folds": fold_results,
            "strategy": strategy,
            "symbol": symbol,
        }

    aggregate = _aggregate_fold_metrics(successful)
    stability = _compute_stability(successful)
    recommendation = _make_recommendation(aggregate, stability, len(successful), n_folds)

    return {
        "ok": True,
        "strategy": strategy,
        "symbol": symbol,
        "n_folds": n_folds,
        "successful_folds": len(successful),
        "mode": mode,
        "folds": fold_results,
        "aggregate": aggregate,
        "stability": stability,
        "recommendation": recommendation,
        "validated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _aggregate_fold_metrics(folds: list[dict]) -> dict:
    """Aggregate OOS metrics across successful folds."""
    accuracies = [f["oos_metrics"]["accuracy"] for f in folds if "oos_metrics" in f]
    n_signals = [f["oos_metrics"]["n_signals"] for f in folds if "oos_metrics" in f]

    return {
        "mean_oos_accuracy": round(statistics.mean(accuracies), 4) if accuracies else 0,
        "median_oos_accuracy": round(statistics.median(accuracies), 4) if accuracies else 0,
        "std_oos_accuracy": round(statistics.stdev(accuracies), 4) if len(accuracies) >= 2 else 0,
        "min_oos_accuracy": round(min(accuracies), 4) if accuracies else 0,
        "max_oos_accuracy": round(max(accuracies), 4) if accuracies else 0,
        "total_oos_signals": sum(n_signals),
        "mean_signals_per_fold": round(statistics.mean(n_signals), 1) if n_signals else 0,
    }


def _compute_stability(folds: list[dict]) -> dict:
    """Measure consistency across folds."""
    accuracies = [f["oos_metrics"]["accuracy"] for f in folds if "oos_metrics" in f]
    if len(accuracies) < 2:
        return {"stable": True, "cv": 0.0, "trend": "insufficient_data"}

    mean_acc = statistics.mean(accuracies)
    std_acc = statistics.stdev(accuracies)
    # Avoid float("inf") — JSON serialization rejects non-finite floats.
    cv = (std_acc / mean_acc) if mean_acc > 1e-12 else (0.0 if std_acc < 1e-12 else 999.0)

    # Check for declining trend (linear regression slope)
    n = len(accuracies)
    x_mean = (n - 1) / 2.0
    y_mean = mean_acc
    num = sum((i - x_mean) * (a - y_mean) for i, a in enumerate(accuracies))
    den = sum((i - x_mean) ** 2 for i in range(n))
    slope = num / den if den > 0 else 0.0

    if slope < -0.02:
        trend = "declining"
    elif slope > 0.02:
        trend = "improving"
    else:
        trend = "stable"

    return {
        "stable": cv < 0.3 and trend != "declining",
        "cv": round(float(cv), 4),
        "slope": round(float(slope), 6),
        "trend": trend,
    }


def _make_recommendation(
    aggregate: dict, stability: dict, n_success: int, n_total: int,
) -> str:
    """Generate deployment recommendation based on WF results."""
    acc = aggregate.get("mean_oos_accuracy", 0)
    signals = aggregate.get("total_oos_signals", 0)
    cv = stability.get("cv", 1.0)
    trend = stability.get("trend", "stable")
    fold_success_rate = n_success / n_total if n_total > 0 else 0

    issues = []
    if acc < 0.35:
        issues.append(f"low OOS accuracy ({acc:.1%})")
    if signals < 10:
        issues.append(f"too few OOS signals ({signals})")
    if cv > 0.4:
        issues.append(f"high variance across folds (CV={cv:.2f})")
    if trend == "declining":
        issues.append("declining accuracy across folds")
    if fold_success_rate < 0.6:
        issues.append(f"only {n_success}/{n_total} folds succeeded")

    if not issues:
        if acc >= 0.5:
            return "DEPLOY — Strong OOS performance with stable walk-forward results"
        return "DEPLOY_WITH_CAUTION — Moderate OOS performance, monitor closely"

    if len(issues) >= 3 or acc < 0.3:
        return f"REJECT — {'; '.join(issues)}"

    return f"REVIEW — {'; '.join(issues)}"
