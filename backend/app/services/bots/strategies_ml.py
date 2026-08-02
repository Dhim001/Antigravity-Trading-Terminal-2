"""ML_SIGNAL_BOOST strategy — XGBoost-based primary signal generator.

Uses a per-symbol HistGradientBoosting 3-class model trained on triple-barrier
labels.  Generates BUY/SELL/NONE signals directly from bar features without
requiring a preceding TA strategy.

Training: call ``train_ml_signal_model()`` with candle history.
Inference: the strategy loads the trained model and predicts on each bar.
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any

import numpy as np

from app.config import BASE_DIR
from app.services.bots.indicators import merge_strategy_config
from app.services.bots.ml_feature_engineering import (
    SIGNAL_FEATURE_NAMES,
    SIGNAL_FEATURE_VERSION,
    TRADE_STATE_FEATURE_VERSION,
    bar_to_signal_features,
    merge_trade_state_features,
    resolve_feature_names,
    resolve_feature_version,
    signal_features_to_vector,
)
from app.services.bots.ml_signal_gates import apply_ml_meta_label_gate
from app.services.bots.ml_triple_barrier import label_distribution, label_triple_barrier
from app.services.bots.strategies import BaseStrategy

logger = logging.getLogger(__name__)

ML_SIGNAL_MODEL_DIR = os.path.join(BASE_DIR, "data", "ml_signal_models")


# ── Model persistence helpers ────────────────────────────────────────────


def _model_dir(symbol: str, timeframe: str | None = None) -> str:
    from app.services.bots.ml_model_artifacts import model_storage_key

    return os.path.join(ML_SIGNAL_MODEL_DIR, model_storage_key(symbol, timeframe))


def _model_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "model.joblib")


def _metadata_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "metadata.json")


def _load_sklearn():
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.metrics import log_loss, accuracy_score
        import joblib
    except ImportError as exc:
        raise RuntimeError(
            "scikit-learn is required for ML signal models (pip install scikit-learn)"
        ) from exc
    return HistGradientBoostingClassifier, accuracy_score, log_loss, joblib


# ── Training pipeline ────────────────────────────────────────────────────


def train_ml_signal_model(
    symbol: str,
    candles: list[dict],
    *,
    config: dict | None = None,
) -> dict[str, Any]:
    """Train a 3-class signal model for a single symbol.

    Parameters
    ----------
    symbol : str
        Trading symbol (e.g. "BTCUSDT").
    candles : list[dict]
        OHLCV bars with indicators already computed.  Must have at minimum:
        close, high, low, volume, ATR_14.  Sorted oldest-first.
    config : dict, optional
        Strategy config overrides.

    Returns
    -------
    dict with ``ok``, ``metrics``, ``label_distribution``, etc.
    """
    raw_cfg = dict(config or {})
    cfg = merge_strategy_config("ML_SIGNAL_BOOST", raw_cfg)
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe

    tf = normalize_model_timeframe(cfg.get("timeframe") or raw_cfg.get("timeframe"))
    cfg["timeframe"] = tf
    # Apply & Retrain / Lab Trigger champion trains must never take the WF/trial path.
    if cfg.get("champion_train") or raw_cfg.get("champion_train"):
        cfg["champion_train"] = True
        cfg.pop("_wf_mode", None)
        cfg.pop("wf_mode", None)
        cfg["skip_persist"] = False
        cfg["skip_snapshot"] = False
    wf_mode = bool(cfg.get("_wf_mode") or cfg.get("wf_mode"))
    if cfg.get("champion_train"):
        wf_mode = False
    wf_parity = bool(cfg.get("wf_capacity_parity", True))
    # Strategy defaults always inject min_train_samples=200 via merge — that
    # crushed lean WF folds. Prefer an explicit Lab override, else WF floor.
    if wf_mode and "min_train_samples" not in raw_cfg:
        min_samples = int(cfg.get("wf_min_train_samples", 80))
    else:
        min_samples = int(cfg.get("min_train_samples", 80 if wf_mode else 200))
    atr_mult = float(cfg.get("triple_barrier_atr_mult", 2.0))
    max_bars = int(cfg.get("triple_barrier_max_bars", 30))
    val_fraction = float(cfg.get("val_fraction", 0.2))
    # UI / Optuna use gbm_max_iter; older callers use max_iter — prefer explicit gbm_*.
    _iter_default = 40 if (wf_mode and not wf_parity) else 150
    if "gbm_max_iter" in cfg:
        max_iter = int(cfg.get("gbm_max_iter", _iter_default))
    else:
        max_iter = int(cfg.get("max_iter", _iter_default))
    # skip_refit: keep train-split weights (no full-series refit into val).
    # Default True for WF folds and when ML calendar holdout is on (Lab champion).
    _cal_skip = False
    try:
        from app.services.bots.ml_data_calendar import calendar_holdout_enabled

        _cal_skip = calendar_holdout_enabled(cfg)
    except Exception:
        pass
    skip_refit = bool(cfg.get("skip_refit", wf_mode or _cal_skip))
    skip_snapshot = bool(cfg.get("skip_snapshot", wf_mode))
    # Persist champion unless WF / skip_live_artifact_writes (decoupled from skip_refit).
    from app.services.bots.ml_training_window import skip_live_artifact_writes

    skip_persist = bool(wf_mode or skip_live_artifact_writes(cfg) or cfg.get("skip_persist"))
    if cfg.get("champion_train"):
        # Apply & Retrain / Lab Trigger must always write the live champion.
        skip_persist = False
        skip_snapshot = False
        wf_mode = False

    # GBM architecture params — config-driven with sensible defaults
    gbm_max_depth = int(cfg.get("gbm_max_depth", 4 if (wf_mode and not wf_parity) else 5))
    gbm_lr = float(cfg.get("gbm_learning_rate", 0.1 if (wf_mode and not wf_parity) else 0.08))
    gbm_l2_reg = float(cfg.get("gbm_l2_reg", 0.0))

    if len(candles) < min_samples + max_bars:
        return {
            "ok": False,
            "error": f"insufficient candles ({len(candles)} < {min_samples + max_bars})",
            "symbol": symbol,
        }

    # Step 1: Label candles with triple-barrier method
    from app.services.bots.ml_feature_cache import (
        resolve_precomputed_features,
        resolve_precomputed_labels,
    )

    labels = resolve_precomputed_labels(candles, cfg)
    if labels is None:
        labels = label_triple_barrier(
            candles,
            atr_mult_upper=atr_mult,
            atr_mult_lower=atr_mult,
            max_holding_bars=max_bars,
        )
    dist = label_distribution(labels)

    # Step 2: Extract features for each labelled bar
    # Match evaluate() deque(maxlen=25) → up to EVAL_FEATURE_LOOKBACK priors.
    from app.services.bots.ml_feature_engineering import EVAL_FEATURE_LOOKBACK

    feature_lookback = EVAL_FEATURE_LOOKBACK
    feature_warmup = 20  # evaluate() starts emitting once hist length >= 20
    rows: list[dict[str, Any]] = []
    include_trade_state = bool(cfg.get("ml_include_trade_state"))
    feat_names = resolve_feature_names(include_trade_state=include_trade_state)
    feat_version = resolve_feature_version(include_trade_state=include_trade_state)
    pre_feat = resolve_precomputed_features(candles, cfg)

    for idx, label_info in enumerate(labels):
        if label_info.get("barrier_hit") == "invalid":
            continue
        # Need at least feature_warmup prior bars (same gate as live evaluate)
        if idx < feature_warmup:
            continue
        # Skip bars too close to the end (they can't have full barrier evaluation)
        if idx >= len(candles) - max_bars:
            continue

        candle = candles[idx]
        if pre_feat is not None and not include_trade_state:
            vector = np.asarray(pre_feat[idx], dtype=np.float64)
        else:
            lookback = candles[max(0, idx - feature_lookback):idx]
            features = bar_to_signal_features(candle, lookback_rows=lookback)
            # Historical train: trade-state features are zeros unless a caller
            # supplies per-bar trade_state on the candle (rare).
            features = merge_trade_state_features(
                features,
                candle.get("trade_state") if isinstance(candle, dict) else None,
                enabled=include_trade_state,
            )
            vector = signal_features_to_vector(
                features, include_trade_state=include_trade_state, feature_names=feat_names,
            )

        rows.append({
            "vector": vector,
            "label": label_info["label"],  # 1 (BUY), -1 (SELL), 0 (NONE)
            "uniqueness": float(label_info.get("uniqueness", 1.0)),
        })

    n = len(rows)
    if n < min_samples:
        return {
            "ok": False,
            "error": f"insufficient labelled samples ({n} < {min_samples})",
            "symbol": symbol,
            "label_distribution": dist,
        }

    # Step 3: Encode labels as 0, 1, 2 for sklearn
    label_map = {1: 0, 0: 1, -1: 2}  # BUY=0, NONE=1, SELL=2
    reverse_map = {0: "BUY", 1: "NONE", 2: "SELL"}

    X = np.vstack([r["vector"] for r in rows])
    y = np.array([label_map[r["label"]] for r in rows], dtype=np.int32)
    sample_weights = np.array([r["uniqueness"] for r in rows], dtype=np.float64)

    # Step 4: Time-ordered train/val split (no shuffling — prevents leakage)
    split_idx = max(1, int(n * (1.0 - val_fraction)))
    if split_idx >= n:
        split_idx = n - 1
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]
    w_train = sample_weights[:split_idx]

    n_classes = len(np.unique(y_train))
    if n_classes < 2:
        return {
            "ok": False,
            "error": f"training set needs at least 2 classes, got {n_classes}",
            "symbol": symbol,
            "label_distribution": dist,
        }

    # Step 5: Train HistGradientBoosting
    HistGBC, accuracy_score, log_loss_fn, joblib = _load_sklearn()

    from app.services.bots.ml_job_progress import (
        cancelled_train_result,
        ml_cancel_requested,
        progress_path_from_config,
    )

    progress_path = progress_path_from_config(cfg)
    if ml_cancel_requested(progress_path):
        return cancelled_train_result(symbol, "ML_SIGNAL_BOOST")

    # Regularization defaults to prevent overfitting on noisy financial bars
    l2_reg = max(0.1, gbm_l2_reg) if gbm_l2_reg > 0 else 0.1
    min_leaf = max(5, int(len(y_train) // 50))

    model = HistGBC(
        max_depth=gbm_max_depth,
        max_iter=max(20, max_iter),
        learning_rate=gbm_lr,
        l2_regularization=l2_reg,
        min_samples_leaf=min_leaf,
        random_state=42,
        class_weight="balanced",
    )
    try:
        model.fit(X_train, y_train, sample_weight=w_train)
    except TypeError:
        model.fit(X_train, y_train)

    # Step 6: Validation & Training metrics
    y_pred_train = model.predict(X_train)
    train_acc = round(float(accuracy_score(y_train, y_pred_train)), 4)

    metrics: dict[str, Any] = {
        "train_samples": int(len(y_train)),
        "val_samples": int(len(y_val)),
        "train_accuracy": train_acc,
    }

    if len(y_val) >= 3 and len(np.unique(y_val)) >= 2:
        y_pred_val = model.predict(X_val)
        proba_val = model.predict_proba(X_val)

        val_acc = round(float(accuracy_score(y_val, y_pred_val)), 4)
        metrics["val_accuracy"] = val_acc
        metrics["overfitting_gap"] = round(max(0.0, train_acc - val_acc), 4)

        try:
            metrics["val_log_loss"] = round(float(log_loss_fn(y_val, proba_val)), 4)
        except ValueError:
            metrics["val_log_loss"] = None

        # Per-class accuracy
        for cls_idx, cls_name in reverse_map.items():
            mask = y_val == cls_idx
            if mask.sum() > 0:
                metrics[f"val_acc_{cls_name.lower()}"] = round(
                    float((y_pred_val[mask] == cls_idx).mean()), 4
                )

    # Step 7: Refit on all data for production inference (skip in WF/PBO folds)
    if not skip_refit:
        if ml_cancel_requested(progress_path):
            return cancelled_train_result(symbol, "ML_SIGNAL_BOOST")
        try:
            model.fit(X, y, sample_weight=sample_weights)
        except TypeError:
            model.fit(X, y)
        metrics["fit_samples"] = int(n)
    else:
        metrics["fit_samples"] = int(len(y_train))

    # Feature importances
    importances = getattr(model, "feature_importances_", None)
    top_features: list[dict[str, Any]] = []
    if importances is not None and len(importances) == len(feat_names):
        pairs = sorted(
            zip(feat_names, importances),
            key=lambda p: p[1],
            reverse=True,
        )
        top_features = [
            {"name": n, "importance": round(float(v), 4)} for n, v in pairs[:10]
        ]

    # Step 8: Persist model + metadata (atomic replace avoids EOF during WF/PBO)
    # Walk-forward / PBO folds must NOT clobber the live champion — keep the fold
    # model in-process for OOS eval only (see inject_session_model).
    if skip_persist:
        metrics["wf_mode"] = True
        session_meta = {
            "symbol": symbol,
            "timeframe": tf,
            "feature_schema_version": feat_version,
            "feature_names": list(feat_names),
            "ml_include_trade_state": include_trade_state,
            "label_map": {str(k): v for k, v in label_map.items()},
            "reverse_map": {str(k): v for k, v in reverse_map.items()},
            "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "sample_count": n,
            "label_distribution": dist,
            "metrics": metrics,
            "top_features": top_features,
            "config": {
                "atr_mult": atr_mult,
                "max_holding_bars": max_bars,
                "min_train_samples": min_samples,
                "gbm_max_depth": gbm_max_depth,
                "gbm_learning_rate": gbm_lr,
                "gbm_max_iter": max(20, max_iter),
                "gbm_l2_reg": gbm_l2_reg,
                "wf_capacity_parity": wf_parity,
                "timeframe": tf,
                "_wf_mode": True,
                "skip_refit": True,
            },
        }
        _signal_model_store.inject_session_model(symbol, model, session_meta, timeframe=tf)
        return {
            "ok": True,
            "symbol": symbol,
            "timeframe": tf,
            **session_meta,
            # Thread-safe OOS path for parallel WF folds (shared store races).
            "_wf_bundle": {
                "strategy": "ML_SIGNAL_BOOST",
                "model": model,
                "metadata": session_meta,
            },
        }

    os.makedirs(_model_dir(symbol, tf), exist_ok=True)
    model_path = _model_path(symbol, tf)
    tmp_path = f"{model_path}.tmp"
    joblib.dump(model, tmp_path)
    os.replace(tmp_path, model_path)

    metadata = {
        "symbol": symbol,
        "timeframe": tf,
        "feature_schema_version": feat_version,
        "feature_names": list(feat_names),
        "ml_include_trade_state": include_trade_state,
        "label_map": {str(k): v for k, v in label_map.items()},
        "reverse_map": {str(k): v for k, v in reverse_map.items()},
        "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sample_count": n,
        "label_distribution": dist,
        "metrics": metrics,
        "top_features": top_features,
        "loss_history": [{
            "epoch": 1,
            "train_loss": metrics.get("val_log_loss"),
            "val_loss": metrics.get("val_log_loss"),
            "val_accuracy": metrics.get("val_accuracy"),
        }] if metrics.get("val_log_loss") is not None else [],
        "config": {
            "atr_mult": atr_mult,
            "max_holding_bars": max_bars,
            "min_train_samples": min_samples,
            "gbm_max_depth": gbm_max_depth,
            "gbm_learning_rate": gbm_lr,
            "gbm_max_iter": max(20, max_iter),
            "gbm_l2_reg": gbm_l2_reg,
            "wf_capacity_parity": wf_parity,
            "timeframe": tf,
            "skip_refit": skip_refit,
            "ml_include_trade_state": include_trade_state,
        },
    }
    cal = cfg.get("_data_calendar")
    if isinstance(cal, dict):
        from app.services.bots.ml_data_calendar import merge_calendar_into_metadata

        metadata = merge_calendar_into_metadata(metadata, cal)
    with open(_metadata_path(symbol, tf), "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)

    try:
        from app.services.bots.ml_model_artifacts import clear_ml_validation_stamp

        clear_ml_validation_stamp(_model_dir(symbol, tf))
    except Exception:
        logger.debug("clear_ml_validation_stamp failed for %s", symbol, exc_info=True)

    # Invalidate cache
    _signal_model_store.invalidate(symbol, timeframe=tf)

    if not skip_snapshot:
        try:
            from app.services.bots.ml_model_artifacts import snapshot_current_version
            snap = snapshot_current_version(_model_dir(symbol, tf), strategy="ML_SIGNAL_BOOST")
            if snap:
                metadata["version_id"] = snap.get("version_id")
                metadata["version_path"] = snap.get("path")
        except Exception:
            logger.exception("Failed to snapshot ML_SIGNAL_BOOST version for %s", symbol)

    logger.info(
        "ML signal model trained for %s @ %s (n=%d, val_acc=%s, dist=%s)",
        symbol,
        tf,
        n,
        metrics.get("val_accuracy"),
        dist,
    )
    return {"ok": True, "symbol": symbol, "timeframe": tf, **metadata}


# ── Model store (in-memory cache) ────────────────────────────────────────


class MlSignalModelStore:
    """In-memory cache of loaded ML signal models (per-symbol) — LRU + TTL."""

    def __init__(self) -> None:
        from app.config import ML_MODEL_CACHE_MAX, ML_MODEL_CACHE_TTL_SEC
        from app.services.bots.model_store_lru import bind_dict_cache

        self._models: dict[str, Any] = {}
        self._metadata: dict[str, dict[str, Any]] = {}
        self._mtime: dict[str, float] = {}
        self._lru = bind_dict_cache(
            self._models, self._metadata, self._mtime,
            max_entries=ML_MODEL_CACHE_MAX,
            ttl_sec=ML_MODEL_CACHE_TTL_SEC,
        )

    def invalidate(self, symbol: str | None = None, *, timeframe: str | None = None) -> None:
        from app.services.bots.ml_model_artifacts import model_storage_key, safe_symbol_key

        if symbol:
            if timeframe is not None:
                sk = model_storage_key(symbol, timeframe)
                prefixes = (sk + "|", sk)
            else:
                sk = safe_symbol_key(symbol)
                prefixes = (sk + "|", sk + "__")
            for p in prefixes:
                self._lru.discard_prefix(p)
            for d in (self._models, self._metadata, self._mtime):
                for k in list(d.keys()):
                    if any(k == p.rstrip("|") or k.startswith(p) for p in prefixes):
                        d.pop(k, None)
        else:
            self._lru.clear()
            self._models.clear()
            self._metadata.clear()
            self._mtime.clear()

    def inject_session_model(
        self,
        symbol: str,
        model: Any,
        metadata: dict[str, Any],
        *,
        timeframe: str | None = None,
    ) -> None:
        """Hold a fold/PBO model in-process without touching the live champion on disk."""
        key = self._cache_key(symbol, None, timeframe)
        self._models[key] = model
        self._metadata[key] = dict(metadata or {})
        self._mtime[key] = -1.0  # sentinel: never treat as disk-backed
        self._lru.touch(key)

    def get_metadata(
        self,
        symbol: str,
        model_version: str | None = None,
        *,
        timeframe: str | None = None,
    ) -> dict[str, Any] | None:
        self._ensure_loaded(symbol, model_version=model_version, timeframe=timeframe)
        return self._metadata.get(self._cache_key(symbol, model_version, timeframe))

    @staticmethod
    def _cache_key(
        symbol: str,
        model_version: str | None,
        timeframe: str | None = None,
    ) -> str:
        from app.services.bots.ml_model_artifacts import model_storage_key

        return f"{model_storage_key(symbol, timeframe)}|{model_version or 'latest'}"

    def predict(
        self,
        symbol: str,
        features: dict[str, float],
        *,
        model_version: str | None = None,
        timeframe: str | None = None,
    ) -> tuple[str, float] | None:
        """Predict signal class and confidence for a symbol.

        Returns
        -------
        tuple of (signal: "BUY"|"SELL"|"NONE", confidence: float) or None if no model.
        """
        model = self._ensure_loaded(
            symbol, model_version=model_version, timeframe=timeframe,
        )
        if model is None:
            return None

        meta = self._metadata.get(self._cache_key(symbol, model_version, timeframe)) or {}
        reverse_map = meta.get("reverse_map", {"0": "BUY", "1": "NONE", "2": "SELL"})
        feat_names = meta.get("feature_names") or list(SIGNAL_FEATURE_NAMES)

        vec = signal_features_to_vector(features, feature_names=feat_names).reshape(1, -1)
        try:
            proba = model.predict_proba(vec)[0]
            pred_idx = int(np.argmax(proba))
            confidence = float(proba[pred_idx])
            signal = reverse_map.get(str(pred_idx), "NONE")
            return signal, confidence
        except Exception as exc:
            logger.warning("ML signal predict failed for %s: %s", symbol, exc)
            return None

    def predict_batch(
        self,
        symbol: str,
        feature_matrix: np.ndarray,
        *,
        model_version: str | None = None,
        timeframe: str | None = None,
        batch_size: int = 512,
        cancel_cb: Any | None = None,
    ) -> list[tuple[str, float] | None]:
        """Batched ``predict_proba`` for backtests — shape ``(N, F)`` → N results."""
        n = int(feature_matrix.shape[0]) if feature_matrix is not None else 0
        if n == 0:
            return []
        model = self._ensure_loaded(
            symbol, model_version=model_version, timeframe=timeframe,
        )
        if model is None:
            return [None] * n

        meta = self._metadata.get(self._cache_key(symbol, model_version, timeframe)) or {}
        reverse_map = meta.get("reverse_map", {"0": "BUY", "1": "NONE", "2": "SELL"})
        out: list[tuple[str, float] | None] = [None] * n
        bs = max(32, int(batch_size or 512))
        mat = np.asarray(feature_matrix, dtype=np.float64)
        for start in range(0, n, bs):
            if cancel_cb is not None and cancel_cb():
                raise InterruptedError("ml_batch_cancel_requested")
            end = min(start + bs, n)
            try:
                proba = model.predict_proba(mat[start:end])
            except Exception as exc:
                logger.warning(
                    "ML signal batch predict failed for %s [%s:%s]: %s",
                    symbol, start, end, exc,
                )
                continue
            for j, row_proba in enumerate(proba):
                pred_idx = int(np.argmax(row_proba))
                out[start + j] = (
                    reverse_map.get(str(pred_idx), "NONE"),
                    float(row_proba[pred_idx]),
                )
        return out

    def _ensure_loaded(
        self,
        symbol: str,
        model_version: str | None = None,
        *,
        timeframe: str | None = None,
    ):
        from app.services.bots.ml_model_artifacts import resolve_model_dir

        key = self._cache_key(symbol, model_version, timeframe)
        # Session-injected WF/PBO models (mtime sentinel -1) skip disk reload.
        if key in self._models and self._mtime.get(key) == -1.0:
            self._lru.touch(key)
            return self._models[key]

        load_dir = resolve_model_dir(_model_dir(symbol, timeframe), model_version)
        path = os.path.join(load_dir, "model.joblib")
        meta_path = os.path.join(load_dir, "metadata.json")

        if not os.path.isfile(path) or not os.path.isfile(meta_path):
            return None

        mtime = os.path.getmtime(path)
        if key in self._models and self._mtime.get(key) == mtime:
            self._lru.touch(key)
            return self._models[key]

        try:
            _, _, _, joblib = _load_sklearn()
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            schema_ver = int(meta.get("feature_schema_version", 0))
            if schema_ver not in (SIGNAL_FEATURE_VERSION, TRADE_STATE_FEATURE_VERSION):
                logger.warning(
                    "ML signal model schema mismatch for %s (got %s) — retrain required",
                    key,
                    schema_ver,
                )
                return None
            model = joblib.load(path)
        except Exception as exc:
            logger.warning("ML signal model load failed for %s: %s", key, exc)
            return None

        self._models[key] = model
        self._metadata[key] = meta
        self._mtime[key] = mtime
        self._lru.touch(key)
        return model


_signal_model_store = MlSignalModelStore()


def train_ml_signal_model_with_config(
    symbol: str,
    candles: list[dict],
    hyperparams: dict | None = None,
    *,
    config: dict | None = None,
) -> dict[str, Any]:
    """Train GBM with an explicit hyperparam dict (used by Optuna auto-tune).

    Merges ``hyperparams`` over ``config`` so sweep trials can override defaults
    without mutating strategy defaults permanently.
    """
    merged = {**(config or {}), **(hyperparams or {})}
    return train_ml_signal_model(symbol, candles, config=merged)


def get_ml_signal_store() -> MlSignalModelStore:
    return _signal_model_store


# ── Strategy class ────────────────────────────────────────────────────────


class MlSignalBoostStrategy(BaseStrategy):
    """XGBoost-based primary signal generator.

    Loads a pre-trained 3-class GBM model for the active symbol and generates
    BUY/SELL/NONE signals directly from bar features.  Falls back to NONE if
    no trained model is available.

    Config keys:
        min_confidence (float): Minimum predicted probability to emit a signal (default 0.55).
        model_symbol (str): Override symbol for model lookup (empty = use bot symbol).
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self._lookback: deque = deque(maxlen=25)
        self._cfg = merge_strategy_config("ML_SIGNAL_BOOST", config or {})

    def _model_timeframe(self) -> str:
        from app.services.bots.ml_model_artifacts import normalize_model_timeframe

        return normalize_model_timeframe(
            self._cfg.get("timeframe") or self.config.get("timeframe")
        )

    def _resolve_symbol(self, df_row) -> str:
        symbol = str(self._cfg.get("model_symbol") or "").strip().upper()
        if not symbol and isinstance(df_row, dict):
            symbol = str(df_row.get("_symbol") or self.config.get("symbol") or "").strip().upper()
        elif not symbol:
            symbol = str(self.config.get("symbol") or "").strip().upper()
        return symbol

    def _include_trade_state(self, symbol: str, store: "MlSignalModelStore") -> bool:
        pinned = self._cfg.get("model_version") or None
        tf = self._model_timeframe()
        meta = store.get_metadata(symbol, pinned or None, timeframe=tf) or {}
        include_ts = bool(meta.get("ml_include_trade_state"))
        if not include_ts:
            try:
                include_ts = int(meta.get("feature_schema_version") or 0) >= TRADE_STATE_FEATURE_VERSION
            except (TypeError, ValueError):
                include_ts = False
        if not meta:
            include_ts = bool(self._cfg.get("ml_include_trade_state"))
        return include_ts

    def _format_prediction(
        self,
        df_row,
        result: tuple[str, float] | None,
        *,
        symbol: str,
        timeframe: str,
    ) -> dict:
        if result is None:
            return {
                "signal": "NONE",
                "reject_reason": "ml_model_missing",
                "reject_detail": f"No trained ML_SIGNAL_BOOST model for {symbol} @ {timeframe}",
            }

        signal, confidence = result
        threshold = float(self._cfg.get("min_confidence", 0.55))
        conf = round(float(confidence), 4)

        atr = df_row.get("ATR_14") if isinstance(df_row, dict) else None
        if atr is None and isinstance(df_row, dict):
            atr = df_row.get("ATRr_14")
        try:
            atr = float(atr or 0)
        except (TypeError, ValueError):
            atr = 0.0

        if signal in ("BUY", "SELL") and conf >= threshold:
            return apply_ml_meta_label_gate({
                "signal": signal,
                "raw_signal": signal,
                "confidence": conf,
                "stop_loss_distance": atr * 1.5 if atr > 0 else None,
                "model_type": "ml_signal_boost",
            }, df_row, self._cfg)

        if signal in ("BUY", "SELL") and conf < threshold:
            return {
                "signal": "NONE",
                "raw_signal": signal,
                "confidence": conf,
                "reject_reason": "ml_confidence",
                "reject_detail": f"confidence {conf:.2f} below min {threshold:.2f}",
            }

        return {
            "signal": "NONE",
            "raw_signal": signal if signal in ("BUY", "SELL", "NONE") else "NONE",
            "confidence": conf,
        }

    def precompute_backtest_signals(
        self,
        rows: list[dict],
        *,
        cancel_cb: Any | None = None,
        progress_cb: Any | None = None,
    ) -> list[dict]:
        """Columnar feature matrix + batched predict for backtests.

        Matches per-bar ``evaluate`` control flow for position-independent
        features (trade_state zeros when absent — same as typical BT rows).
        Uses ``BACKTEST_VECTORIZED_FEATURES`` (default on); live ``evaluate``
        stays on per-bar ``bar_to_signal_features``.
        """
        from app.services.bots.ml_batch_inference import inference_batch_size
        from app.services.bots.ml_feature_engineering import (
            EVAL_FEATURE_LOOKBACK,
            precompute_signal_feature_matrix,
            vectorized_features_enabled,
        )

        n = len(rows)
        out: list[dict] = [
            {
                "signal": "NONE",
                "reject_reason": "ml_warmup",
                "reject_detail": "Need >= 20 bars of lookback for ML features",
            }
            for _ in range(n)
        ]
        if n < 20:
            return out

        symbol = self._resolve_symbol(rows[-1] if rows else {})
        if not symbol:
            missing = {
                "signal": "NONE",
                "reject_reason": "ml_symbol_missing",
                "reject_detail": "No model_symbol / symbol for ML model lookup",
            }
            return [dict(missing) for _ in range(n)]

        store = get_ml_signal_store()
        pinned = self._cfg.get("model_version") or None
        tf = self._model_timeframe()
        include_ts = self._include_trade_state(symbol, store)
        meta = store.get_metadata(symbol, pinned or None, timeframe=tf) or {}
        feat_names = meta.get("feature_names") or list(
            resolve_feature_names(include_trade_state=include_ts)
        )
        base_names = list(SIGNAL_FEATURE_NAMES)
        # Columnar path covers SIGNAL_FEATURE_NAMES; trade-state dims appended after.
        use_matrix = vectorized_features_enabled() and (
            not include_ts
            or list(feat_names[: len(base_names)]) == base_names
        )

        lookback: deque = deque(maxlen=25)
        if use_matrix:
            mat = precompute_signal_feature_matrix(
                rows,
                feature_lookback=EVAL_FEATURE_LOOKBACK,
                cancel_cb=cancel_cb,
                progress_cb=progress_cb,
            )
            vectors: list[np.ndarray | None] = [None] * n
            for i in range(n):
                if cancel_cb is not None and i % 512 == 0 and cancel_cb():
                    raise InterruptedError("ml_batch_cancel_requested")
                lookback.append(dict(rows[i]))
                if i < 19:
                    continue
                vec = mat[i].astype(np.float64, copy=False)
                if include_ts:
                    trade_state = rows[i].get("trade_state") or rows[i].get("pretrade_context")
                    features = {name: float(vec[j]) for j, name in enumerate(base_names)}
                    features = merge_trade_state_features(
                        features, trade_state, enabled=True,
                    )
                    vectors[i] = signal_features_to_vector(
                        features, feature_names=feat_names,
                    )
                elif list(feat_names) == base_names:
                    vectors[i] = vec
                else:
                    features = {name: float(vec[j]) for j, name in enumerate(base_names)}
                    vectors[i] = signal_features_to_vector(
                        features, feature_names=feat_names,
                    )
        else:
            # Legacy deque path (flag off or exotic feature_names).
            vectors = [None] * n
            report_every = max(512, n // 20)
            for i, row in enumerate(rows):
                if cancel_cb is not None and i % 512 == 0 and cancel_cb():
                    raise InterruptedError("ml_batch_cancel_requested")
                if progress_cb is not None and (i + 1) % report_every == 0:
                    try:
                        progress_cb(i + 1, n)
                    except Exception:
                        pass
                lookback.append(dict(row))
                if len(lookback) < 20:
                    continue
                lookback_list = list(lookback)[:-1]
                features = bar_to_signal_features(row, lookback_rows=lookback_list)
                trade_state = row.get("trade_state") or row.get("pretrade_context")
                features = merge_trade_state_features(
                    features, trade_state, enabled=include_ts,
                )
                vectors[i] = signal_features_to_vector(features, feature_names=feat_names)

        warm_idx = [i for i, v in enumerate(vectors) if v is not None]
        if not warm_idx:
            self._lookback = lookback
            return out

        feat_mat = np.stack([vectors[i] for i in warm_idx], axis=0)
        batch_size = inference_batch_size(self._cfg)
        preds = store.predict_batch(
            symbol,
            feat_mat,
            model_version=pinned or None,
            timeframe=tf,
            batch_size=batch_size,
            cancel_cb=cancel_cb,
        )
        for pred, i in zip(preds, warm_idx):
            out[i] = self._format_prediction(
                rows[i], pred, symbol=symbol, timeframe=tf,
            )
        self._lookback = lookback
        return out

    def evaluate(self, df_row) -> dict:
        # Maintain lookback window for rolling features
        self._lookback.append(dict(df_row))

        # Need enough lookback bars for feature computation
        if len(self._lookback) < 20:
            return {
                "signal": "NONE",
                "reject_reason": "ml_warmup",
                "reject_detail": "Need >= 20 bars of lookback for ML features",
            }

        symbol = self._resolve_symbol(df_row)
        if not symbol:
            return {
                "signal": "NONE",
                "reject_reason": "ml_symbol_missing",
                "reject_detail": "No model_symbol / symbol for ML model lookup",
            }

        # Extract features with lookback
        lookback_list = list(self._lookback)[:-1]  # all except current
        features = bar_to_signal_features(df_row, lookback_rows=lookback_list)

        # Align trade-state dims to the loaded model (not only bot config) so a
        # v4 artifact never gets v5 keys in drift logs, and a v5 model gets
        # live trade_state from eval_row when the manager injects it.
        store = get_ml_signal_store()
        pinned = self._cfg.get("model_version") or None
        tf = self._model_timeframe()
        include_ts = self._include_trade_state(symbol, store)

        trade_state = None
        if isinstance(df_row, dict):
            trade_state = df_row.get("trade_state") or df_row.get("pretrade_context")
        features = merge_trade_state_features(
            features, trade_state, enabled=include_ts,
        )

        from app.services.bots.ml_feature_drift import record_ml_inference_features

        record_ml_inference_features(symbol, "ML_SIGNAL_BOOST", features)

        result = store.predict(
            symbol, features, model_version=pinned or None, timeframe=tf,
        )
        return self._format_prediction(df_row, result, symbol=symbol, timeframe=tf)
