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
import time
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from app.services.bots.backtest_purged_cv import (
    apply_embargo_after_test,
    embargo_bars_for_segment,
    estimate_purge_bars,
)
from app.services.bots.ml_registry import (
    ENSEMBLE_STRATEGIES,
    ML_STRATEGIES,
    _trainer_cache as _TRAINER_REGISTRY,
    get_trainer,
    is_ensemble_strategy,
    is_ml_strategy,
)
from app.services.bots.ml_triple_barrier import label_triple_barrier

logger = logging.getLogger(__name__)

# Re-export for back-compat
__all__ = [
    "ML_STRATEGIES",
    "ENSEMBLE_STRATEGIES",
    "get_trainer",
    "is_ml_strategy",
    "is_ensemble_strategy",
    "_TRAINER_REGISTRY",
]

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
    *,
    progress_pct: int | None = None,
    progress_phase: str | None = None,
) -> dict[str, Any]:
    """Run a trained strategy over OOS candles and compute metrics.

    Returns dict with: accuracy, n_signals, buy_count, sell_count, none_count.
    RL_PPO_AGENT uses episode return (not triple-barrier classification accuracy).
    """
    key = str(strategy_cls or "").upper()
    bundle = (train_result or {}).get("_wf_bundle") if isinstance(train_result, dict) else None
    if isinstance(bundle, dict) and bundle.get("strategy") in (
        "TRANSFORMER_SIGNAL",
        "LSTM_DIRECTION",
    ):
        return _evaluate_oos_transformer_torch(test_candles, bundle, config or {})

    if key == "RL_PPO_AGENT":
        return _evaluate_oos_rl_env(test_candles, train_result, config or {})

    # Fast path: batch predict for XGB signal model (avoids per-bar strategy overhead).
    if key == "ML_SIGNAL_BOOST":
        try:
            return _evaluate_oos_ml_signal_batch(
                test_candles, config or {}, train_result=train_result,
            )
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
    from app.services.bots.ml_event_sampling import (
        annotate_event_labels,
        clamp_uniqueness,
        directional_hit,
        finalize_oos_metrics,
        should_score_oos_event,
    )

    labels = annotate_event_labels(labels, test_candles, config)

    counts = {"BUY": 0, "SELL": 0, "NONE": 0}
    raw_correct = 0
    raw_total = 0
    w_correct = 0.0
    w_total = 0.0

    # Stride long OOS windows only in lean WF so validation stays responsive.
    # Capacity parity scores every bar (dense eval matches production intent).
    stride = 1
    if (
        bool((config or {}).get("_wf_mode"))
        and not bool((config or {}).get("wf_capacity_parity", True))
        and len(test_candles) > 400
    ):
        stride = max(1, len(test_candles) // 400)

    # Dense per-bar eval can run for many minutes on deep-net strategies —
    # heartbeat so the job never looks frozen between fold-phase writes.
    from app.services.bots.ml_job_progress import (
        progress_path_from_config,
        write_ml_progress,
    )

    hb_path = progress_path_from_config(config)
    hb_t = 0.0
    n_bars = len(test_candles)

    for i, candle in enumerate(test_candles):
        if hb_path and progress_pct is not None:
            now = time.monotonic()
            if now - hb_t >= 2.0:
                hb_t = now
                write_ml_progress(
                    hb_path,
                    pct=progress_pct,
                    phase=progress_phase or "oos",
                    detail=f"oos {i + 1}/{n_bars}",
                )
        result = strat.evaluate(candle)
        if i % stride != 0:
            continue
        signal = result.get("signal", "NONE")
        counts[signal] = counts.get(signal, 0) + 1

        if signal == "NONE":
            continue

        if i < len(labels):
            lbl = labels[i]
            hit = directional_hit(signal, lbl)
            raw_total += 1
            if hit:
                raw_correct += 1
            if should_score_oos_event(lbl, config):
                u = clamp_uniqueness(lbl.get("uniqueness", 1.0))
                w_total += u
                if hit:
                    w_correct += u

    tot_bars = len(test_candles)
    return finalize_oos_metrics(
        raw_correct=raw_correct,
        raw_total=raw_total,
        weighted_correct=w_correct,
        weighted_total=w_total,
        counts=counts,
        total_bars=tot_bars,
    )


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
    # OOS must walk the full fold once (not a random capped train window).
    from app.services.bots.rl_risk import resolve_rl_costs

    oos_cfg = dict(config or {})
    oos_cfg["max_episode_steps"] = max(len(test_candles), 8)
    oos_cfg["env_seed"] = int(oos_cfg.get("env_seed") or 0)
    fee_bps, slip_bps = resolve_rl_costs(oos_cfg)
    oos_cfg["fee_bps"] = fee_bps
    oos_cfg["slippage_bps"] = slip_bps
    env = TradingEnv(
        test_candles,
        config=oos_cfg,
        feat_mean=feat_mean,
        feat_std=feat_std,
    )

    from app.services.bots.rl_risk import resolve_min_confidence, greedy_serve_action

    if isinstance(bundle, dict) and bundle.get("min_confidence") is not None:
        threshold = resolve_min_confidence(bundle)
    else:
        threshold = resolve_min_confidence(config)

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
            action = greedy_serve_action(
                logits.detach().cpu().numpy(), min_confidence=threshold,
            )
        action_counts[action] = action_counts.get(action, 0) + 1
        obs, _reward, done, _info = env.step(action)
        steps += 1
        if steps > len(test_candles) + 5:
            break

    stats = env.episode_stats()
    return_pct = float(stats.get("return_pct") or 0.0)
    trades = int(stats.get("total_trades") or 0)
    score = _rl_return_to_score(return_pct)
    avg_win = float(stats.get("avg_win") or 0.0)
    avg_loss = float(stats.get("avg_loss") or 0.0)
    profit_factor = float(stats.get("profit_factor") or 0.0)
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
        "avg_win": round(avg_win, 6),
        "avg_loss": round(avg_loss, 6),
        "profit_factor": round(profit_factor, 4),
        "n_wins": int(stats.get("n_wins") or 0),
        "n_losses": int(stats.get("n_losses") or 0),
        "fee_bps": stats.get("fee_bps"),
        "slippage_bps": stats.get("slippage_bps"),
    }


def _evaluate_oos_transformer_torch(
    test_candles: list[dict],
    bundle: dict,
    config: dict,
) -> dict[str, Any]:
    """In-memory Transformer / LSTM OOS eval — no ONNX reload between WF folds."""
    from app.services.bots.ml_feature_cache import (
        resolve_precomputed_features,
        resolve_precomputed_labels,
    )
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

    from app.services.bots.ml_feature_engineering import EVAL_FEATURE_LOOKBACK

    threshold = float(bundle.get("min_confidence") or config.get("min_confidence") or 0.55)
    feature_lb = EVAL_FEATURE_LOOKBACK
    feature_warmup = 20
    max_bars = int(config.get("triple_barrier_max_bars", 30))
    labels = resolve_precomputed_labels(test_candles, config)
    if labels is None:
        labels = label_triple_barrier(
            test_candles,
            atr_mult_upper=float(config.get("triple_barrier_atr_mult", 2.0)),
            atr_mult_lower=float(config.get("triple_barrier_atr_mult", 2.0)),
            max_holding_bars=max_bars,
        )
    from app.services.bots.ml_event_sampling import (
        annotate_event_labels,
        clamp_uniqueness,
        directional_hit,
        finalize_oos_metrics,
        should_score_oos_event,
    )

    labels = annotate_event_labels(labels, test_candles, config)

    stride = 1
    if len(test_candles) > 400:
        stride = max(1, len(test_candles) // 400)

    model.eval()
    counts = {"BUY": 0, "SELL": 0, "NONE": 0}
    raw_correct = 0
    raw_total = 0
    w_correct = 0.0
    w_total = 0.0
    feat_matrix = resolve_precomputed_features(test_candles, config)
    if feat_matrix is None:
        feat_matrix = precompute_signal_feature_matrix(
            test_candles, feature_lookback=feature_lb,
        )
    from app.services.bots.ml_feature_engineering import (
        apply_exclude_features,
        resolve_exclude_features,
    )

    feat_matrix = apply_exclude_features(feat_matrix, resolve_exclude_features(config))

    with torch.no_grad():
        for i in range(lookback + feature_warmup, len(test_candles)):
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
                lbl = labels[i]
                hit = directional_hit(signal, lbl)
                raw_total += 1
                if hit:
                    raw_correct += 1
                if should_score_oos_event(lbl, config):
                    u = clamp_uniqueness(lbl.get("uniqueness", 1.0))
                    w_total += u
                    if hit:
                        w_correct += u

    return finalize_oos_metrics(
        raw_correct=raw_correct,
        raw_total=raw_total,
        weighted_correct=w_correct,
        weighted_total=w_total,
        counts=counts,
        total_bars=len(test_candles),
    )


def _predict_ml_signal_from_bundle(
    bundle: dict,
    features: dict[str, float],
) -> tuple[str, float] | None:
    """Predict using an in-memory WF fold model (thread-safe vs shared store)."""
    model = bundle.get("model")
    meta = bundle.get("metadata") if isinstance(bundle.get("metadata"), dict) else {}
    if model is None:
        return None
    from app.services.bots.ml_feature_engineering import (
        SIGNAL_FEATURE_NAMES,
        signal_features_to_vector,
    )

    reverse_map = meta.get("reverse_map", {"0": "BUY", "1": "NONE", "2": "SELL"})
    feat_names = meta.get("feature_names") or list(SIGNAL_FEATURE_NAMES)
    vec = signal_features_to_vector(
        features,
        feature_names=feat_names,
        exclude_features=meta.get("exclude_features"),
    ).reshape(1, -1)
    try:
        proba = model.predict_proba(vec)[0]
        pred_idx = int(np.argmax(proba))
        confidence = float(proba[pred_idx])
        signal = reverse_map.get(str(pred_idx), "NONE")
        return signal, confidence
    except Exception as exc:
        logger.warning("ML signal bundle predict failed: %s", exc)
        return None


def _evaluate_oos_ml_signal_batch(
    test_candles: list[dict],
    config: dict,
    *,
    train_result: dict | None = None,
) -> dict[str, Any]:
    """Vectorized OOS accuracy for ML_SIGNAL_BOOST using fold bundle or store."""
    from app.services.bots.ml_feature_cache import (
        resolve_precomputed_features,
        resolve_precomputed_labels,
    )
    from app.services.bots.ml_feature_engineering import (
        EVAL_FEATURE_LOOKBACK,
        SIGNAL_FEATURE_NAMES,
        bar_to_signal_features,
    )
    from app.services.bots.strategies_ml import get_ml_signal_store

    symbol = str(config.get("model_symbol") or config.get("symbol") or "").upper()
    if not symbol:
        raise ValueError("symbol required for batch OOS")

    store = get_ml_signal_store()
    threshold = float(config.get("min_confidence", 0.55))
    feature_lookback = EVAL_FEATURE_LOOKBACK
    feature_warmup = 20
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe

    tf = normalize_model_timeframe(config.get("timeframe"))
    bundle = (train_result or {}).get("_wf_bundle") if isinstance(train_result, dict) else None
    use_bundle = (
        isinstance(bundle, dict)
        and bundle.get("strategy") == "ML_SIGNAL_BOOST"
        and bundle.get("model") is not None
    )
    labels = resolve_precomputed_labels(test_candles, config)
    if labels is None:
        labels = label_triple_barrier(
            test_candles,
            atr_mult_upper=float(config.get("triple_barrier_atr_mult", 2.0)),
            atr_mult_lower=float(config.get("triple_barrier_atr_mult", 2.0)),
            max_holding_bars=int(config.get("triple_barrier_max_bars", 30)),
        )
    from app.services.bots.ml_event_sampling import (
        annotate_event_labels,
        clamp_uniqueness,
        directional_hit,
        finalize_oos_metrics,
        should_score_oos_event,
    )

    labels = annotate_event_labels(labels, test_candles, config)

    feat_matrix = resolve_precomputed_features(test_candles, config)
    meta = (bundle or {}).get("metadata") if use_bundle else None
    feat_names = (
        (meta or {}).get("feature_names") if isinstance(meta, dict) else None
    ) or list(SIGNAL_FEATURE_NAMES)

    counts = {"BUY": 0, "SELL": 0, "NONE": 0}
    raw_correct = 0
    raw_total = 0
    w_correct = 0.0
    w_total = 0.0

    for i, candle in enumerate(test_candles):
        if i < feature_warmup:
            counts["NONE"] += 1
            continue
        if feat_matrix is not None:
            features = {
                name: float(feat_matrix[i, j])
                for j, name in enumerate(feat_names)
                if j < feat_matrix.shape[1]
            }
        else:
            lookback = test_candles[max(0, i - feature_lookback):i]
            features = bar_to_signal_features(candle, lookback_rows=lookback)
        if use_bundle:
            pred = _predict_ml_signal_from_bundle(bundle, features)
        else:
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
            lbl = labels[i]
            hit = directional_hit(signal, lbl)
            raw_total += 1
            if hit:
                raw_correct += 1
            if should_score_oos_event(lbl, config):
                u = clamp_uniqueness(lbl.get("uniqueness", 1.0))
                w_total += u
                if hit:
                    w_correct += u

    return finalize_oos_metrics(
        raw_correct=raw_correct,
        raw_total=raw_total,
        weighted_correct=w_correct,
        weighted_total=w_total,
        counts=counts,
        total_bars=len(test_candles),
    )


# ── Main walk-forward runner ──────────────────────────────────────────────

# CPU/GBM strategies may run folds via ThreadPool. GPU/deep stay sequential
# to avoid VRAM contention and nested ProcessPool on Windows.
CPU_PARALLEL_WF_STRATEGIES = frozenset({"ML_SIGNAL_BOOST"})


def _purge_train_indices(
    indices: list[int],
    purge_bars: int,
) -> tuple[list[int], dict[str, Any]]:
    """Mirror ``purge_train_before_test`` on index lists for feature gathering."""
    from app.services.bots.backtest_purged_cv import MIN_TRAIN_BARS

    purge_bars = max(0, int(purge_bars or 0))
    if purge_bars <= 0 or not indices:
        return list(indices), {"purge_bars": 0, "purged": False}
    if len(indices) <= purge_bars + MIN_TRAIN_BARS // 2:
        keep = max(MIN_TRAIN_BARS // 2, len(indices) // 2)
        return indices[:keep], {
            "purge_bars": purge_bars,
            "purged": True,
            "truncated_to": keep,
            "note": "Train shortened to preserve minimum IS size",
        }
    return indices[:-purge_bars], {
        "purge_bars": purge_bars,
        "purged": True,
        "removed_bars": purge_bars,
    }


def _resolve_wf_fold_workers(
    strategy: str,
    n_folds: int,
    cfg: dict | None = None,
) -> int:
    """ThreadPool size for WF folds (1 = sequential).

    Shipped default is sequential (``ML_WF_FOLD_WORKERS=1``). ``auto`` scales
    up to ``min(n_folds, 4, cpu)``. Nested Optuna-trial ThreadPools must stay
    sequential via ``_disable_wf_fold_parallel``.
    """
    key = str(strategy or "").upper()
    if key not in CPU_PARALLEL_WF_STRATEGIES:
        return 1
    conf = cfg if isinstance(cfg, dict) else {}
    if conf.get("_disable_wf_fold_parallel"):
        return 1
    try:
        from app.config import ML_WF_FOLD_WORKERS

        raw = (ML_WF_FOLD_WORKERS or "1").strip().lower()
    except Exception:
        raw = "1"
    if raw in ("auto", "scale"):
        import os

        cpu = os.cpu_count() or 4
        return max(1, min(int(n_folds), 4, cpu))
    try:
        return max(1, min(int(raw), int(n_folds)))
    except (TypeError, ValueError):
        return 1


def _prepare_wf_fold_jobs(
    folds: list[dict],
    candles: list[dict],
    symbol: str,
    cfg: dict,
    feature_cache: Any | None,
) -> list[dict[str, Any]]:
    """Precompute per-fold train/test windows (purge/embargo) before execution."""
    jobs: list[dict[str, Any]] = []
    prev_test_start: int | None = None
    prev_test_end: int | None = None

    for fold in folds:
        train_start = int(fold["train_start"])
        train_end = int(fold["train_end"])
        test_start = int(fold["test_start"])
        test_end = int(fold["test_end"])
        embargo_bars = int(fold.get("embargo_bars") or 0)

        embargo_info: dict[str, Any] = {"embargo_bars": embargo_bars, "applied": False}
        if prev_test_end is not None and prev_test_start is not None:
            embargo_until = apply_embargo_after_test(candles, prev_test_end, embargo_bars)
            embargo_info["prev_test_start"] = prev_test_start
            embargo_info["prev_test_end"] = prev_test_end
            embargo_info["embargo_until"] = embargo_until
            train_indices: list[int] = []
            if train_start < prev_test_start:
                train_indices.extend(range(train_start, min(train_end, prev_test_start)))
            if embargo_until < train_end:
                train_indices.extend(range(max(train_start, embargo_until), train_end))
            embargo_info["applied"] = True
            embargo_info["train_bars_after_embargo"] = len(train_indices)
        else:
            train_indices = list(range(train_start, train_end))

        test_indices = list(range(test_start, test_end))
        train_indices, purge_info = _purge_train_indices(
            train_indices, int(fold["purge_bars"]),
        )
        purge_info = {**(purge_info or {}), "embargo": embargo_info}

        train_candles = [candles[i] for i in train_indices]
        test_candles = [candles[i] for i in test_indices]
        for row in train_candles:
            if isinstance(row, dict):
                row.setdefault("_symbol", symbol)
        for row in test_candles:
            if isinstance(row, dict):
                row.setdefault("_symbol", symbol)

        fold_cfg = dict(cfg)
        oos_feats = None
        oos_labels = None
        if feature_cache is not None:
            fold_cfg = feature_cache.attach_config(fold_cfg, train_indices)
            # Keep OOS cache on the job (not fold_cfg) so trainers never see test feats.
            test_gathered = feature_cache.gather(test_indices)
            oos_feats = test_gathered["features"]
            oos_labels = test_gathered["labels"]

        jobs.append({
            "fold": fold,
            "train_candles": train_candles,
            "test_candles": test_candles,
            "purge_info": purge_info,
            "fold_cfg": fold_cfg,
            "_oos_precomputed_features": oos_feats,
            "_oos_precomputed_labels": oos_labels,
        })
        prev_test_start = test_start
        prev_test_end = test_end
    return jobs


def _run_single_wf_fold(
    *,
    strategy: str,
    symbol: str,
    trainer: Callable,
    job: dict[str, Any],
    progress_path: str | None,
    fold_num: int,
    n_fold_total: int,
) -> dict[str, Any]:
    """Train + OOS-eval one prepared fold."""
    from app.services.bots.ml_job_progress import ml_cancel_requested, write_ml_progress

    fold = job["fold"]
    train_candles = job["train_candles"]
    test_candles = job["test_candles"]
    purge_info = job["purge_info"]
    fold_cfg = dict(job["fold_cfg"])

    if ml_cancel_requested(progress_path):
        return {
            "fold": fold["fold"],
            "ok": False,
            "cancelled": True,
            "error": "cancelled",
            "train_bars": len(train_candles),
            "test_bars": len(test_candles),
            "purge": purge_info,
        }

    write_ml_progress(
        progress_path,
        pct=int(5 + (fold_num - 1) / max(1, n_fold_total) * 80),
        phase=f"fold {fold_num}/{n_fold_total}",
        detail="training",
    )

    if len(train_candles) < 50:
        return {
            "fold": fold["fold"],
            "ok": False,
            "error": (
                f"Train window too small after purge/embargo ({len(train_candles)} bars)"
            ),
            "train_bars": len(train_candles),
            "test_bars": len(test_candles),
            "purge": purge_info,
        }

    try:
        train_result = trainer(symbol, train_candles, config=fold_cfg)
    except Exception as exc:
        logger.warning("WF fold %d train failed: %s", fold["fold"], exc)
        return {
            "fold": fold["fold"],
            "ok": False,
            "error": str(exc),
            "train_bars": len(train_candles),
            "test_bars": len(test_candles),
            "purge": purge_info,
        }

    if not train_result.get("ok", False):
        return {
            "fold": fold["fold"],
            "ok": False,
            "error": train_result.get("error", "Training failed"),
            "train_bars": len(train_candles),
            "test_bars": len(test_candles),
            "purge": purge_info,
        }

    write_ml_progress(
        progress_path,
        pct=int(5 + (fold_num - 0.35) / max(1, n_fold_total) * 80),
        phase=f"fold {fold_num}/{n_fold_total}",
        detail="oos",
    )

    oos_cfg = dict(fold_cfg)
    # Strip any train-side cache, then attach OOS-only features from the job.
    oos_cfg.pop("_precomputed_features", None)
    oos_cfg.pop("_precomputed_labels", None)
    oos_feats = job.get("_oos_precomputed_features")
    oos_labels = job.get("_oos_precomputed_labels")
    if oos_feats is not None:
        oos_cfg["_precomputed_features"] = oos_feats
    if oos_labels is not None:
        oos_cfg["_precomputed_labels"] = oos_labels

    try:
        oos_metrics = evaluate_oos_accuracy(
            strategy, test_candles, oos_cfg, train_result=train_result,
            progress_pct=int(5 + (fold_num - 0.35) / max(1, n_fold_total) * 80),
            progress_phase=f"fold {fold_num}/{n_fold_total}",
        )
    except Exception as exc:
        logger.warning("WF fold %d OOS eval failed: %s", fold["fold"], exc)
        if isinstance(train_result, dict):
            train_result.pop("_wf_bundle", None)
        return {
            "fold": fold["fold"],
            "ok": False,
            "error": f"OOS eval failed: {exc}",
            "train_bars": len(train_candles),
            "test_bars": len(test_candles),
            "train_metrics": train_result.get("metrics", {}),
            "purge": purge_info,
        }

    if isinstance(train_result, dict):
        train_result.pop("_wf_bundle", None)

    train_metrics = train_result.get("metrics", {})
    if isinstance(train_metrics, dict) and str(strategy).upper() == "RL_PPO_AGENT":
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

    result = {
        "fold": fold["fold"],
        "ok": True,
        "train_bars": len(train_candles),
        "test_bars": len(test_candles),
        "accuracy": oos_metrics.get("accuracy"),
        "n_samples": oos_metrics.get("n_signals"),
        "train_metrics": train_metrics,
        "oos_metrics": oos_metrics,
        "purge": purge_info,
    }
    write_ml_progress(
        progress_path,
        pct=min(90, int(5 + fold_num / max(1, n_fold_total) * 80)),
        phase=f"fold {fold_num}/{n_fold_total}",
        detail=(
            f"ret={oos_metrics.get('return_pct')}%"
            if oos_metrics.get("metric_kind") == "rl_return"
            else f"acc={oos_metrics.get('accuracy')}"
        ),
    )
    return result


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

    from app.services.bots.ml_job_progress import (
        ml_cancel_requested,
        progress_path_from_config,
    )

    progress_path = progress_path_from_config(cfg)
    n_fold_total = max(1, len(folds))

    # Opt #4: precompute features/labels once for the full series.
    feature_cache = None
    try:
        from app.services.bots.ml_feature_cache import WfFeatureCache

        feature_cache = WfFeatureCache(candles, cfg)
    except Exception:
        logger.warning("WfFeatureCache build failed — per-fold recompute", exc_info=True)

    jobs = _prepare_wf_fold_jobs(folds, candles, symbol, cfg, feature_cache)
    fold_workers = _resolve_wf_fold_workers(strategy, len(jobs), cfg)
    fold_results: list[dict[str, Any]] = []

    job_id = str(cfg.get("job_id") or cfg.get("_ml_job_id") or "") or None
    done_folds: set[int] = set()
    prior_folds: list[dict] = []
    if job_id:
        try:
            from app.services.bots.ml_job_checkpoint import (
                completed_fold_indices,
                empty_wf_checkpoint,
            )
            from app.services.bots.ml_job_store import load_ml_job_checkpoint, save_ml_job_checkpoint

            prior_cp = load_ml_job_checkpoint(job_id)
            if isinstance(prior_cp, dict) and prior_cp.get("kind") == "walk_forward":
                done_folds = completed_fold_indices(prior_cp)
                prior_folds = list(prior_cp.get("fold_results") or [])
                fold_results.extend(prior_folds)
            else:
                save_ml_job_checkpoint(
                    job_id,
                    empty_wf_checkpoint(
                        job_id=job_id,
                        strategy=strategy,
                        symbol=symbol,
                        config=cfg,
                        n_folds=n_fold_total,
                    ),
                )
        except Exception:
            logger.debug("WF checkpoint hydrate failed", exc_info=True)

    def _persist_wf_fold(fold_idx: int, entry: dict) -> None:
        if not job_id:
            return
        try:
            from app.services.bots.ml_job_checkpoint import merge_wf_fold
            from app.services.bots.ml_job_store import load_ml_job_checkpoint, save_ml_job_checkpoint

            save_ml_job_checkpoint(
                job_id,
                merge_wf_fold(
                    load_ml_job_checkpoint(job_id),
                    job_id=job_id,
                    strategy=strategy,
                    symbol=symbol,
                    config=cfg,
                    n_folds=n_fold_total,
                    fold_idx=fold_idx,
                    fold_entry=entry,
                ),
            )
        except Exception:
            logger.debug("WF fold checkpoint save failed", exc_info=True)

    if fold_workers <= 1 or len(jobs) <= 1:
        for i, job in enumerate(jobs):
            if i in done_folds:
                continue
            if ml_cancel_requested(progress_path):
                return {
                    "ok": False,
                    "cancelled": True,
                    "error": "cancelled",
                    "folds": fold_results,
                    "strategy": strategy,
                    "symbol": symbol,
                }
            entry = _run_single_wf_fold(
                strategy=strategy,
                symbol=symbol,
                trainer=trainer,
                job=job,
                progress_path=progress_path,
                fold_num=i + 1,
                n_fold_total=n_fold_total,
            )
            fold_results.append(entry)
            _persist_wf_fold(i, entry)
            if entry.get("cancelled"):
                return {
                    "ok": False,
                    "cancelled": True,
                    "error": "cancelled",
                    "folds": [f for f in fold_results if not f.get("cancelled")],
                    "strategy": strategy,
                    "symbol": symbol,
                }
    else:
        # Opt #2: ThreadPool for CPU/GBM folds (no nested ProcessPool).
        from concurrent.futures import ThreadPoolExecutor, as_completed

        ordered: dict[int, dict[str, Any]] = {}
        cancel_hit = False
        pending_jobs = [(i, job) for i, job in enumerate(jobs) if i not in done_folds]
        with ThreadPoolExecutor(max_workers=fold_workers) as pool:
            futures = {
                pool.submit(
                    _run_single_wf_fold,
                    strategy=strategy,
                    symbol=symbol,
                    trainer=trainer,
                    job=job,
                    progress_path=progress_path,
                    fold_num=i + 1,
                    n_fold_total=n_fold_total,
                ): i
                for i, job in pending_jobs
            }
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    ordered[idx] = fut.result()
                except Exception as exc:
                    fold = jobs[idx]["fold"]
                    ordered[idx] = {
                        "fold": fold["fold"],
                        "ok": False,
                        "error": str(exc),
                        "train_bars": len(jobs[idx]["train_candles"]),
                        "test_bars": len(jobs[idx]["test_candles"]),
                        "purge": jobs[idx]["purge_info"],
                    }
                _persist_wf_fold(idx, ordered[idx])
                if ordered[idx].get("cancelled") or ml_cancel_requested(progress_path):
                    cancel_hit = True
                    for pending in futures:
                        pending.cancel()
                    break
        # Seed ordered with prior checkpoint folds.
        for entry in prior_folds:
            try:
                fi = int(entry.get("fold") or 0) - 1
            except (TypeError, ValueError):
                continue
            if fi >= 0 and fi not in ordered:
                ordered[fi] = entry
        # After executor shutdown (waits for in-flight work), drain any results
        # that finished after we broke out of as_completed on cancel.
        if cancel_hit:
            for fut, idx in futures.items():
                if idx in ordered or fut.cancelled() or not fut.done():
                    continue
                try:
                    ordered[idx] = fut.result()
                    _persist_wf_fold(idx, ordered[idx])
                except Exception as exc:
                    fold = jobs[idx]["fold"]
                    ordered[idx] = {
                        "fold": fold["fold"],
                        "ok": False,
                        "error": str(exc),
                        "train_bars": len(jobs[idx]["train_candles"]),
                        "test_bars": len(jobs[idx]["test_candles"]),
                        "purge": jobs[idx]["purge_info"],
                    }
                    _persist_wf_fold(idx, ordered[idx])
        fold_results = [ordered[i] for i in sorted(ordered)]
        if cancel_hit or any(f.get("cancelled") for f in fold_results):
            return {
                "ok": False,
                "cancelled": True,
                "error": "cancelled",
                "folds": [f for f in fold_results if not f.get("cancelled")],
                "strategy": strategy,
                "symbol": symbol,
            }
        if len(ordered) != len(jobs):
            return {
                "ok": False,
                "error": (
                    f"Incomplete parallel fold results "
                    f"({len(ordered)}/{len(jobs)} folds collected)"
                ),
                "folds": fold_results,
                "strategy": strategy,
                "symbol": symbol,
            }

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
        pfs = [
            float(f["oos_metrics"]["profit_factor"])
            for f in folds
            if isinstance(f.get("oos_metrics"), dict)
            and f["oos_metrics"].get("profit_factor") is not None
        ]
        wins = [
            float(f["oos_metrics"]["avg_win"])
            for f in folds
            if isinstance(f.get("oos_metrics"), dict)
            and f["oos_metrics"].get("avg_win") is not None
        ]
        losses = [
            float(f["oos_metrics"]["avg_loss"])
            for f in folds
            if isinstance(f.get("oos_metrics"), dict)
            and f["oos_metrics"].get("avg_loss") is not None
        ]
        if pfs:
            out["mean_oos_profit_factor"] = round(statistics.mean(pfs), 4)
        if wins:
            out["mean_oos_avg_win"] = round(statistics.mean(wins), 6)
        if losses:
            out["mean_oos_avg_loss"] = round(statistics.mean(losses), 6)
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
        from app.services.bots.rl_risk import MIN_PROFIT_FACTOR, payoff_passes

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
        pf_ok, pf_msg = payoff_passes(
            avg_win=aggregate.get("mean_oos_avg_win"),
            avg_loss=aggregate.get("mean_oos_avg_loss"),
            profit_factor=aggregate.get("mean_oos_profit_factor"),
            min_pf=MIN_PROFIT_FACTOR,
        )
        if not pf_ok:
            issues.append(pf_msg)

        if not issues:
            if ret >= 2.0 and pos_folds >= max(1, n_success // 2):
                return (
                    "DEPLOY — Costed OOS payoff passes (avg win ≥ avg loss, "
                    f"PF > {MIN_PROFIT_FACTOR}) · mean {ret:.2f}%. Paper first."
                )
            return (
                "DEPLOY_WITH_CAUTION — Payoff gate passed; paper-trade before live "
                f"(mean {ret:.2f}%)"
            )
        if len(issues) >= 3 or ret < -5.0 or not pf_ok:
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
