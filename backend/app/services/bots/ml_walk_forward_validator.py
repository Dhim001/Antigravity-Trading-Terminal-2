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
    apply_embargo_after_test,
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

ENSEMBLE_STRATEGIES = frozenset({"HYBRID_ENSEMBLE"})


def is_ml_strategy(strategy: str) -> bool:
    """True for train/validate artifact strategies (not the hybrid ensemble wrapper)."""
    return str(strategy).upper() in ML_STRATEGIES


def is_ensemble_strategy(strategy: str) -> bool:
    return str(strategy).upper() in ENSEMBLE_STRATEGIES

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
    train_result: dict | None = None,
) -> dict[str, Any]:
    """Run a trained strategy over OOS candles and compute metrics.

    Returns dict with: accuracy, n_signals, buy_count, sell_count, none_count.
    RL_PPO_AGENT uses episode return (not triple-barrier classification accuracy).
    """
    key = str(strategy_cls or "").upper()
    bundle = (train_result or {}).get("_wf_bundle") if isinstance(train_result, dict) else None
    if isinstance(bundle, dict) and bundle.get("strategy") == "TRANSFORMER_SIGNAL":
        return _evaluate_oos_transformer_torch(test_candles, bundle, config or {})

    if key == "RL_PPO_AGENT":
        return _evaluate_oos_rl_env(test_candles, train_result, config or {})

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

    # Stride long OOS windows only in lean WF so validation stays responsive.
    # Capacity parity scores every bar (dense eval matches production intent).
    stride = 1
    if (
        bool((config or {}).get("_wf_mode"))
        and not bool((config or {}).get("wf_capacity_parity", True))
        and len(test_candles) > 400
    ):
        stride = max(1, len(test_candles) // 400)

    for i, candle in enumerate(test_candles):
        result = strat.evaluate(candle)
        if i % stride != 0:
            continue
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
    tot_bars = len(test_candles)
    sig_count = counts.get("BUY", 0) + counts.get("SELL", 0)
    signal_rate = round(sig_count / tot_bars, 4) if tot_bars > 0 else 0.0

    return {
        "accuracy": round(accuracy, 4),
        "n_signals": total,
        "n_correct": correct,
        "buy_count": counts.get("BUY", 0),
        "sell_count": counts.get("SELL", 0),
        "none_count": counts.get("NONE", 0),
        "signal_rate": signal_rate,
        "total_bars": tot_bars,
    }


def _rl_return_to_score(return_pct: float) -> float:
    """Map episode return % onto a 0–1 score (0% → 0.5) for shared WF gates."""
    # Soft saturating map: ±10% ≈ 0.27 / 0.73, ± return ≈ 0.5.
    return float(1.0 / (1.0 + math.exp(-float(return_pct) / 5.0)))


def _evaluate_oos_rl_env(
    test_candles: list[dict],
    train_result: dict | None,
    config: dict,
) -> dict[str, Any]:
    """Score PPO folds with TradingEnv episode return (not triple-barrier labels)."""
    import torch

    from app.services.bots.rl_trading_env import (
        ACTION_BUY,
        ACTION_CLOSE,
        ACTION_HOLD,
        ACTION_SELL,
        TradingEnv,
    )

    if len(test_candles) < 40:
        raise ValueError(f"RL OOS needs ≥40 candles, got {len(test_candles)}")

    bundle = (train_result or {}).get("_wf_bundle") if isinstance(train_result, dict) else None
    model = bundle.get("model") if isinstance(bundle, dict) else None
    scaler = bundle.get("scaler") if isinstance(bundle, dict) else None
    if model is None:
        raise ValueError(
            "RL OOS requires in-memory fold policy (_wf_bundle). "
            "Re-run Validate — fold trains must skip live ONNX overwrite."
        )

    feat_mean = (scaler or {}).get("feat_mean")
    feat_std = (scaler or {}).get("feat_std")
    env = TradingEnv(
        test_candles,
        config=config,
        feat_mean=feat_mean,
        feat_std=feat_std,
    )

    model.eval()
    device = next(model.parameters()).device
    obs = env.reset()
    done = False
    action_counts = {
        ACTION_HOLD: 0,
        ACTION_BUY: 0,
        ACTION_SELL: 0,
        ACTION_CLOSE: 0,
    }
    steps = 0
    while not done:
        with torch.no_grad():
            x = torch.tensor(obs, dtype=torch.float32, device=device).unsqueeze(0)
            logits = model(x)
            action = int(torch.argmax(logits, dim=-1).item())
        action_counts[action] = action_counts.get(action, 0) + 1
        obs, _reward, done, _info = env.step(action)
        steps += 1
        if steps > len(test_candles) + 5:
            break

    stats = env.episode_stats()
    return_pct = float(stats.get("return_pct") or 0.0)
    trades = int(stats.get("total_trades") or 0)
    score = _rl_return_to_score(return_pct)
    tot_bars = len(test_candles)
    buy_n = int(action_counts.get(ACTION_BUY, 0))
    sell_n = int(action_counts.get(ACTION_SELL, 0))
    close_n = int(action_counts.get(ACTION_CLOSE, 0))
    hold_n = int(action_counts.get(ACTION_HOLD, 0))
    active = buy_n + sell_n + close_n

    return {
        "metric_kind": "rl_return",
        # Compatibility field for existing aggregate / deploy display.
        "accuracy": round(score, 4),
        "return_pct": round(return_pct, 4),
        "final_equity": float(stats.get("final_equity") or 1.0),
        "n_signals": trades,
        "n_correct": trades if return_pct > 0 else 0,
        "buy_count": buy_n,
        "sell_count": sell_n,
        "none_count": hold_n,
        "close_count": close_n,
        "signal_rate": round(active / tot_bars, 4) if tot_bars > 0 else 0.0,
        "total_bars": tot_bars,
        "total_trades": trades,
        "oos_steps": steps,
    }


def _evaluate_oos_transformer_torch(
    test_candles: list[dict],
    bundle: dict,
    config: dict,
) -> dict[str, Any]:
    """In-memory Transformer OOS eval — no ONNX reload between WF folds."""
    from app.services.bots.ml_feature_engineering import precompute_signal_feature_matrix

    import torch

    model = bundle.get("model")
    if model is None:
        raise ValueError("transformer wf bundle missing model")
    lookback = int(bundle.get("lookback") or config.get("lookback") or 60)
    mean = np.asarray(bundle.get("mean"), dtype=np.float32)
    std = np.asarray(bundle.get("std"), dtype=np.float32)
    std = np.where(std < 1e-8, 1.0, std)
    reverse_map = bundle.get("reverse_map") or {0: "BUY", 1: "NONE", 2: "SELL"}
    # reverse_map may be int->str or str->str
    def _cls_to_signal(cls_idx: int) -> str:
        if cls_idx in reverse_map:
            return str(reverse_map[cls_idx])
        return str(reverse_map.get(str(cls_idx), "NONE"))

    threshold = float(bundle.get("min_confidence") or config.get("min_confidence") or 0.55)
    feature_lb = 20
    max_bars = int(config.get("triple_barrier_max_bars", 30))
    labels = label_triple_barrier(
        test_candles,
        atr_mult_upper=float(config.get("triple_barrier_atr_mult", 2.0)),
        atr_mult_lower=float(config.get("triple_barrier_atr_mult", 2.0)),
        max_holding_bars=max_bars,
    )

    stride = 1
    if len(test_candles) > 400:
        stride = max(1, len(test_candles) // 400)

    model.eval()
    correct = 0
    total = 0
    counts = {"BUY": 0, "SELL": 0, "NONE": 0}
    feat_matrix = precompute_signal_feature_matrix(
        test_candles, feature_lookback=feature_lb,
    )

    with torch.no_grad():
        for i in range(lookback + feature_lb, len(test_candles)):
            if i % stride != 0:
                continue
            x = feat_matrix[i - lookback + 1 : i + 1].astype(np.float32, copy=True)
            x = (x - mean) / std
            logits = model(torch.from_numpy(x).unsqueeze(0))
            probs = torch.softmax(logits, dim=-1)[0]
            cls_idx = int(torch.argmax(probs).item())
            conf = float(probs[cls_idx].item())
            signal = _cls_to_signal(cls_idx)
            if signal not in ("BUY", "SELL") or conf < threshold:
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
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe

    tf = normalize_model_timeframe(config.get("timeframe"))
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
        pred = store.predict(
            symbol,
            features,
            model_version=config.get("model_version") or None,
            timeframe=tf,
        )
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
    # Walk-forward folds: skip live artifact writes. Capacity defaults to
    # production parity unless the caller opts into lean/fast mode.
    cfg.setdefault("_wf_mode", True)
    cfg.setdefault("wf_capacity_parity", True)
    trainer = get_trainer(strategy)
    if trainer is None:
        return {
            "ok": False,
            "error": f"No trainer registered for {strategy}",
            "strategy": strategy,
            "symbol": symbol,
        }

    max_holding = max(1, int(cfg.get("triple_barrier_max_bars", 30)))
    purge_bars = max(estimate_purge_bars(cfg), max_holding)
    n = len(candles)
    tf = str(cfg.get("timeframe") or "1m")
    try:
        from app.services.bots.ml_training_window import wf_adaptive_fold_mins

        min_train, min_test = wf_adaptive_fold_mins(n, tf)
    except Exception:
        min_train, min_test = 200, 100
    folds = generate_wf_folds(
        n, n_folds=n_folds, mode=mode,
        purge_bars=purge_bars, embargo_pct=embargo_pct,
        min_train=min_train, min_test=min_test,
    )

    if not folds:
        return {
            "ok": False,
            "error": (
                f"Insufficient data for {n_folds}-fold WF ({n} candles; "
                f"need ≥{min_train + min_test + purge_bars} with "
                f"min_train={min_train}, min_test={min_test}). "
                f"Increase Training window or lower timeframe."
            ),
            "strategy": strategy,
            "symbol": symbol,
        }

    fold_results = []
    prev_test_start: int | None = None
    prev_test_end: int | None = None

    from app.services.bots.ml_job_progress import (
        ml_cancel_requested,
        progress_path_from_config,
        write_ml_progress,
    )

    progress_path = progress_path_from_config(cfg)
    n_fold_total = max(1, len(folds))

    for fold in folds:
        if ml_cancel_requested(progress_path):
            return {
                "ok": False,
                "cancelled": True,
                "error": "cancelled",
                "folds": fold_results,
                "strategy": strategy,
                "symbol": symbol,
            }

        fold_num = int(fold.get("fold") or len(fold_results) + 1)
        pct = int(5 + (fold_num - 1) / n_fold_total * 80)
        write_ml_progress(
            progress_path,
            pct=pct,
            phase=f"fold {fold_num}/{n_fold_total}",
            detail="training",
        )

        train_start = int(fold["train_start"])
        train_end = int(fold["train_end"])
        test_start = int(fold["test_start"])
        test_end = int(fold["test_end"])
        embargo_bars = int(fold.get("embargo_bars") or 0)

        # Post-test embargo: strip prior OOS (+ embargo buffer) from the next IS window
        # so labels / features from fold i's test do not leak into fold i+1 train.
        embargo_info: dict[str, Any] = {"embargo_bars": embargo_bars, "applied": False}
        if prev_test_end is not None and prev_test_start is not None:
            embargo_until = apply_embargo_after_test(candles, prev_test_end, embargo_bars)
            embargo_info["prev_test_start"] = prev_test_start
            embargo_info["prev_test_end"] = prev_test_end
            embargo_info["embargo_until"] = embargo_until
            parts: list[dict] = []
            if train_start < prev_test_start:
                parts.extend(candles[train_start:min(train_end, prev_test_start)])
            if embargo_until < train_end:
                parts.extend(candles[max(train_start, embargo_until):train_end])
            train_candles = parts
            embargo_info["applied"] = True
            embargo_info["train_bars_after_embargo"] = len(train_candles)
        else:
            train_candles = candles[train_start:train_end]

        test_candles = candles[test_start:test_end]
        for row in train_candles:
            if isinstance(row, dict):
                row.setdefault("_symbol", symbol)
        for row in test_candles:
            if isinstance(row, dict):
                row.setdefault("_symbol", symbol)

        # Purge overlap between this fold's train end and its own test start
        train_candles, purge_info = purge_train_before_test(
            train_candles, test_candles, fold["purge_bars"],
        )
        purge_info = {**(purge_info or {}), "embargo": embargo_info}

        if len(train_candles) < 50:
            fold_results.append({
                "fold": fold["fold"],
                "ok": False,
                "error": (
                    f"Train window too small after purge/embargo ({len(train_candles)} bars)"
                ),
                "train_bars": len(train_candles),
                "test_bars": len(test_candles),
                "purge": purge_info,
            })
            prev_test_start = test_start
            prev_test_end = test_end
            continue

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
                "purge": purge_info,
            })
            prev_test_start = test_start
            prev_test_end = test_end
            continue

        if not train_result.get("ok", False):
            fold_results.append({
                "fold": fold["fold"],
                "ok": False,
                "error": train_result.get("error", "Training failed"),
                "train_bars": len(train_candles),
                "test_bars": len(test_candles),
                "purge": purge_info,
            })
            prev_test_start = test_start
            prev_test_end = test_end
            continue

        # Evaluate on OOS fold (must not abort the whole WF run)
        write_ml_progress(
            progress_path,
            pct=int(5 + (fold_num - 0.35) / n_fold_total * 80),
            phase=f"fold {fold_num}/{n_fold_total}",
            detail="oos",
        )
        try:
            oos_metrics = evaluate_oos_accuracy(
                strategy, test_candles, cfg, train_result=train_result,
            )
        except Exception as exc:
            logger.warning("WF fold %d OOS eval failed: %s", fold["fold"], exc)
            if isinstance(train_result, dict):
                train_result.pop("_wf_bundle", None)
            fold_results.append({
                "fold": fold["fold"],
                "ok": False,
                "error": f"OOS eval failed: {exc}",
                "train_bars": len(train_candles),
                "test_bars": len(test_candles),
                "train_metrics": train_result.get("metrics", {}),
                "purge": purge_info,
            })
            prev_test_start = test_start
            prev_test_end = test_end
            continue

        if isinstance(train_result, dict):
            train_result.pop("_wf_bundle", None)

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
        write_ml_progress(
            progress_path,
            pct=min(90, int(5 + fold_num / n_fold_total * 80)),
            phase=f"fold {fold_num}/{n_fold_total}",
            detail=(
                f"ret={oos_metrics.get('return_pct')}%"
                if oos_metrics.get("metric_kind") == "rl_return"
                else f"acc={oos_metrics.get('accuracy')}"
            ),
        )
        prev_test_start = test_start
        prev_test_end = test_end

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

    # Check for capacity gap warning
    wf_parity = bool(cfg.get("wf_capacity_parity", True))
    capacity_gap_warning = None
    if not wf_parity:
        metric_word = (
            "OOS returns"
            if str(strategy).upper() == "RL_PPO_AGENT"
            or (isinstance(aggregate, dict) and aggregate.get("metric_kind") == "rl_return")
            else "OOS accuracy"
        )
        capacity_gap_warning = (
            "Walk-forward validation used reduced hyperparams (fast mode). "
            f"{metric_word} may not reflect production model performance. "
            "Enable wf_capacity_parity for accurate validation."
        )

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
        "wf_capacity_parity": wf_parity,
        "capacity_gap_warning": capacity_gap_warning,
    }


def _aggregate_fold_metrics(folds: list[dict]) -> dict:
    """Aggregate OOS metrics across successful folds."""
    accuracies = [f["oos_metrics"]["accuracy"] for f in folds if "oos_metrics" in f]
    n_signals = [f["oos_metrics"]["n_signals"] for f in folds if "oos_metrics" in f]
    rl_returns = [
        float(f["oos_metrics"]["return_pct"])
        for f in folds
        if isinstance(f.get("oos_metrics"), dict)
        and f["oos_metrics"].get("metric_kind") == "rl_return"
        and f["oos_metrics"].get("return_pct") is not None
    ]

    out = {
        "mean_oos_accuracy": round(statistics.mean(accuracies), 4) if accuracies else 0,
        "median_oos_accuracy": round(statistics.median(accuracies), 4) if accuracies else 0,
        "std_oos_accuracy": round(statistics.stdev(accuracies), 4) if len(accuracies) >= 2 else 0,
        "min_oos_accuracy": round(min(accuracies), 4) if accuracies else 0,
        "max_oos_accuracy": round(max(accuracies), 4) if accuracies else 0,
        "total_oos_signals": sum(n_signals),
        "mean_signals_per_fold": round(statistics.mean(n_signals), 1) if n_signals else 0,
    }
    if rl_returns:
        out["metric_kind"] = "rl_return"
        out["mean_oos_return_pct"] = round(statistics.mean(rl_returns), 4)
        out["median_oos_return_pct"] = round(statistics.median(rl_returns), 4)
        out["std_oos_return_pct"] = (
            round(statistics.stdev(rl_returns), 4) if len(rl_returns) >= 2 else 0.0
        )
        out["min_oos_return_pct"] = round(min(rl_returns), 4)
        out["max_oos_return_pct"] = round(max(rl_returns), 4)
        out["positive_return_folds"] = sum(1 for r in rl_returns if r > 0)
    return out


def _compute_stability(folds: list[dict]) -> dict:
    """Measure consistency across folds."""
    rl_mode = any(
        isinstance(f.get("oos_metrics"), dict) and f["oos_metrics"].get("metric_kind") == "rl_return"
        for f in folds
    )
    if rl_mode:
        series = [
            float(f["oos_metrics"]["return_pct"])
            for f in folds
            if isinstance(f.get("oos_metrics"), dict)
            and f["oos_metrics"].get("return_pct") is not None
        ]
    else:
        series = [f["oos_metrics"]["accuracy"] for f in folds if "oos_metrics" in f]

    if len(series) < 2:
        return {"stable": True, "cv": 0.0, "trend": "insufficient_data", "metric": "rl_return" if rl_mode else "accuracy"}

    mean_v = statistics.mean(series)
    std_v = statistics.stdev(series)
    # Avoid float("inf") — JSON serialization rejects non-finite floats.
    scale = abs(mean_v) if rl_mode else mean_v
    cv = (std_v / scale) if abs(scale) > 1e-12 else (0.0 if std_v < 1e-12 else 999.0)

    # Check for declining trend (linear regression slope)
    n = len(series)
    x_mean = (n - 1) / 2.0
    y_mean = mean_v
    num = sum((i - x_mean) * (a - y_mean) for i, a in enumerate(series))
    den = sum((i - x_mean) ** 2 for i in range(n))
    slope = num / den if den > 0 else 0.0

    # RL returns are %-scale; accuracy is 0–1.
    decline_thr = -0.5 if rl_mode else -0.02
    improve_thr = 0.5 if rl_mode else 0.02
    if slope < decline_thr:
        trend = "declining"
    elif slope > improve_thr:
        trend = "improving"
    else:
        trend = "stable"

    return {
        "stable": cv < (0.8 if rl_mode else 0.3) and trend != "declining",
        "cv": round(float(cv), 4),
        "slope": round(float(slope), 6),
        "trend": trend,
        "metric": "rl_return" if rl_mode else "accuracy",
    }


def _make_recommendation(
    aggregate: dict, stability: dict, n_success: int, n_total: int,
) -> str:
    """Generate deployment recommendation based on WF results."""
    fold_success_rate = n_success / n_total if n_total > 0 else 0
    cv = stability.get("cv", 1.0)
    trend = stability.get("trend", "stable")

    if aggregate.get("metric_kind") == "rl_return":
        ret = float(aggregate.get("mean_oos_return_pct") or 0.0)
        trades = int(aggregate.get("total_oos_signals") or 0)
        pos_folds = int(aggregate.get("positive_return_folds") or 0)
        issues = []
        if ret < -1.0:
            issues.append(f"negative mean OOS return ({ret:.2f}%)")
        if trades < 4:
            issues.append(f"too few OOS trades ({trades})")
        if cv > 1.0:
            issues.append(f"high return variance across folds (CV={cv:.2f})")
        if trend == "declining":
            issues.append("declining OOS returns across folds")
        if fold_success_rate < 0.6:
            issues.append(f"only {n_success}/{n_total} folds succeeded")
        if pos_folds == 0 and n_success > 0:
            issues.append("no fold produced a positive OOS return")

        if not issues:
            if ret >= 2.0 and pos_folds >= max(1, n_success // 2):
                return (
                    "DEPLOY — Positive OOS episode returns with stable walk-forward "
                    f"(mean {ret:.2f}%)"
                )
            return (
                "DEPLOY_WITH_CAUTION — Modest OOS returns; monitor live paper closely "
                f"(mean {ret:.2f}%)"
            )
        if len(issues) >= 3 or ret < -5.0:
            return f"REJECT — {'; '.join(issues)}"
        return f"REVIEW — {'; '.join(issues)}"

    acc = aggregate.get("mean_oos_accuracy", 0)
    signals = aggregate.get("total_oos_signals", 0)

    issues = []
    if acc < 0.42:
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
