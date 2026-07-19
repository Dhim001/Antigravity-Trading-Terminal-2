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
    bar_to_signal_features,
    signal_features_to_vector,
)
from app.services.bots.ml_signal_gates import apply_ml_meta_label_gate
from app.services.bots.ml_triple_barrier import label_distribution, label_triple_barrier
from app.services.bots.strategies import BaseStrategy

logger = logging.getLogger(__name__)

ML_SIGNAL_MODEL_DIR = os.path.join(BASE_DIR, "data", "ml_signal_models")


# ── Model persistence helpers ────────────────────────────────────────────


def _model_dir(symbol: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(symbol).upper())
    return os.path.join(ML_SIGNAL_MODEL_DIR, safe)


def _model_path(symbol: str) -> str:
    return os.path.join(_model_dir(symbol), "model.joblib")


def _metadata_path(symbol: str) -> str:
    return os.path.join(_model_dir(symbol), "metadata.json")


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
    cfg = merge_strategy_config("ML_SIGNAL_BOOST", config or {})
    wf_mode = bool(cfg.get("_wf_mode") or cfg.get("wf_mode"))
    wf_parity = bool(cfg.get("wf_capacity_parity", True))
    min_samples = int(cfg.get("min_train_samples", 80 if wf_mode else 200))
    atr_mult = float(cfg.get("triple_barrier_atr_mult", 2.0))
    max_bars = int(cfg.get("triple_barrier_max_bars", 30))
    val_fraction = float(cfg.get("val_fraction", 0.2))
    max_iter = int(cfg.get("max_iter", 40 if (wf_mode and not wf_parity) else 150))
    skip_refit = bool(cfg.get("skip_refit", wf_mode))
    skip_snapshot = bool(cfg.get("skip_snapshot", wf_mode))

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
    labels = label_triple_barrier(
        candles,
        atr_mult_upper=atr_mult,
        atr_mult_lower=atr_mult,
        max_holding_bars=max_bars,
    )
    dist = label_distribution(labels)

    # Step 2: Extract features for each labelled bar
    # Build lookback window for rolling features
    lookback_size = 20
    rows: list[dict[str, Any]] = []

    for idx, label_info in enumerate(labels):
        if label_info.get("barrier_hit") == "invalid":
            continue
        # Need at least lookback_size prior bars for features
        if idx < lookback_size:
            continue
        # Skip bars too close to the end (they can't have full barrier evaluation)
        if idx >= len(candles) - max_bars:
            continue

        candle = candles[idx]
        lookback = candles[max(0, idx - lookback_size):idx]
        features = bar_to_signal_features(candle, lookback_rows=lookback)
        vector = signal_features_to_vector(features)

        rows.append({
            "vector": vector,
            "label": label_info["label"],  # 1 (BUY), -1 (SELL), 0 (NONE)
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

    # Step 4: Time-ordered train/val split (no shuffling — prevents leakage)
    split_idx = max(1, int(n * (1.0 - val_fraction)))
    if split_idx >= n:
        split_idx = n - 1
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

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

    model = HistGBC(
        max_depth=gbm_max_depth,
        max_iter=max(20, max_iter),
        learning_rate=gbm_lr,
        l2_regularization=gbm_l2_reg,
        min_samples_leaf=max(2, min_samples // 20),
        random_state=42,
        class_weight="balanced",
    )
    model.fit(X_train, y_train)

    # Step 6: Validation metrics
    metrics: dict[str, Any] = {
        "train_samples": int(len(y_train)),
        "val_samples": int(len(y_val)),
    }

    if len(y_val) >= 3 and len(np.unique(y_val)) >= 2:
        y_pred_val = model.predict(X_val)
        proba_val = model.predict_proba(X_val)

        metrics["val_accuracy"] = round(float(accuracy_score(y_val, y_pred_val)), 4)
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
        model.fit(X, y)
        metrics["fit_samples"] = int(n)
    else:
        metrics["fit_samples"] = int(len(y_train))
        metrics["wf_mode"] = True

    # Feature importances
    importances = getattr(model, "feature_importances_", None)
    top_features: list[dict[str, Any]] = []
    if importances is not None and len(importances) == len(SIGNAL_FEATURE_NAMES):
        pairs = sorted(
            zip(SIGNAL_FEATURE_NAMES, importances),
            key=lambda p: p[1],
            reverse=True,
        )
        top_features = [
            {"name": n, "importance": round(float(v), 4)} for n, v in pairs[:10]
        ]

    # Step 8: Persist model + metadata (atomic replace avoids EOF during WF/PBO)
    os.makedirs(_model_dir(symbol), exist_ok=True)
    model_path = _model_path(symbol)
    tmp_path = f"{model_path}.tmp"
    joblib.dump(model, tmp_path)
    os.replace(tmp_path, model_path)

    metadata = {
        "symbol": symbol,
        "feature_schema_version": SIGNAL_FEATURE_VERSION,
        "feature_names": list(SIGNAL_FEATURE_NAMES),
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
        },
    }
    with open(_metadata_path(symbol), "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)

    # Invalidate cache
    _signal_model_store.invalidate(symbol)

    if not skip_snapshot:
        try:
            from app.services.bots.ml_model_artifacts import snapshot_current_version
            snap = snapshot_current_version(_model_dir(symbol), strategy="ML_SIGNAL_BOOST")
            if snap:
                metadata["version_id"] = snap.get("version_id")
                metadata["version_path"] = snap.get("path")
        except Exception:
            logger.exception("Failed to snapshot ML_SIGNAL_BOOST version for %s", symbol)

    logger.info(
        "ML signal model trained for %s (n=%d, val_acc=%s, dist=%s)",
        symbol,
        n,
        metrics.get("val_accuracy"),
        dist,
    )
    return {"ok": True, "symbol": symbol, **metadata}


# ── Model store (in-memory cache) ────────────────────────────────────────


class MlSignalModelStore:
    """In-memory cache of loaded ML signal models (per-symbol)."""

    def __init__(self) -> None:
        self._models: dict[str, Any] = {}
        self._metadata: dict[str, dict[str, Any]] = {}
        self._mtime: dict[str, float] = {}

    def invalidate(self, symbol: str | None = None) -> None:
        if symbol:
            prefix = str(symbol).upper() + "|"
            for d in (self._models, self._metadata, self._mtime):
                for k in list(d.keys()):
                    if k == str(symbol).upper() or k.startswith(prefix):
                        d.pop(k, None)
        else:
            self._models.clear()
            self._metadata.clear()
            self._mtime.clear()

    def get_metadata(self, symbol: str, model_version: str | None = None) -> dict[str, Any] | None:
        self._ensure_loaded(symbol, model_version=model_version)
        return self._metadata.get(self._cache_key(symbol, model_version))

    @staticmethod
    def _cache_key(symbol: str, model_version: str | None) -> str:
        return f"{str(symbol).upper()}|{model_version or 'latest'}"

    def predict(
        self,
        symbol: str,
        features: dict[str, float],
        *,
        model_version: str | None = None,
    ) -> tuple[str, float] | None:
        """Predict signal class and confidence for a symbol.

        Returns
        -------
        tuple of (signal: "BUY"|"SELL"|"NONE", confidence: float) or None if no model.
        """
        model = self._ensure_loaded(symbol, model_version=model_version)
        if model is None:
            return None

        meta = self._metadata.get(self._cache_key(symbol, model_version)) or {}
        reverse_map = meta.get("reverse_map", {"0": "BUY", "1": "NONE", "2": "SELL"})

        vec = signal_features_to_vector(features).reshape(1, -1)
        try:
            proba = model.predict_proba(vec)[0]
            pred_idx = int(np.argmax(proba))
            confidence = float(proba[pred_idx])
            signal = reverse_map.get(str(pred_idx), "NONE")
            return signal, confidence
        except Exception as exc:
            logger.warning("ML signal predict failed for %s: %s", symbol, exc)
            return None

    def _ensure_loaded(self, symbol: str, model_version: str | None = None):
        from app.services.bots.ml_model_artifacts import resolve_model_dir

        key = self._cache_key(symbol, model_version)
        load_dir = resolve_model_dir(_model_dir(symbol), model_version)
        path = os.path.join(load_dir, "model.joblib")
        meta_path = os.path.join(load_dir, "metadata.json")

        if not os.path.isfile(path) or not os.path.isfile(meta_path):
            return None

        mtime = os.path.getmtime(path)
        if key in self._models and self._mtime.get(key) == mtime:
            return self._models[key]

        try:
            _, _, _, joblib = _load_sklearn()
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            if int(meta.get("feature_schema_version", 0)) != SIGNAL_FEATURE_VERSION:
                logger.warning(
                    "ML signal model schema mismatch for %s — retrain required", key
                )
                return None
            model = joblib.load(path)
        except Exception as exc:
            logger.warning("ML signal model load failed for %s: %s", key, exc)
            return None

        self._models[key] = model
        self._metadata[key] = meta
        self._mtime[key] = mtime
        return model


_signal_model_store = MlSignalModelStore()


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

        symbol = str(self._cfg.get("model_symbol") or "").strip().upper()
        if not symbol:
            symbol = str(df_row.get("_symbol") or self.config.get("symbol") or "").strip().upper()
        if not symbol:
            return {
                "signal": "NONE",
                "reject_reason": "ml_symbol_missing",
                "reject_detail": "No model_symbol / symbol for ML model lookup",
            }

        # Extract features with lookback
        lookback_list = list(self._lookback)[:-1]  # all except current
        features = bar_to_signal_features(df_row, lookback_rows=lookback_list)

        # Predict
        store = get_ml_signal_store()
        pinned = self._cfg.get("model_version") or None
        result = store.predict(symbol, features, model_version=pinned or None)
        if result is None:
            return {
                "signal": "NONE",
                "reject_reason": "ml_model_missing",
                "reject_detail": f"No trained ML_SIGNAL_BOOST model for {symbol}",
            }

        signal, confidence = result
        threshold = float(self._cfg.get("min_confidence", 0.55))
        conf = round(float(confidence), 4)

        atr = df_row.get("ATR_14") or df_row.get("ATRr_14") or 0
        try:
            atr = float(atr)
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
