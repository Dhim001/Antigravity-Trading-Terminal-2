"""Gradient-boosted meta-label classifier — predict P(win) for entry setups."""

from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np

from app.config import BASE_DIR, META_LABEL_MIN_TRAIN_SAMPLES, META_LABEL_MODEL_DIR
from app.services.bots.calibration import (
    confidence_bucket,
    fetch_bot_timeframes,
    fetch_trades_for_calibration,
    pair_closed_trades,
    score_bucket,
)

logger = logging.getLogger(__name__)

FEATURE_SCHEMA_VERSION = 1

FEATURE_NAMES: tuple[str, ...] = (
    "score",
    "confidence",
    "abs_score",
    "trend_score",
    "momentum_score",
    "volume_score",
    "sentiment_domain_score",
    "sentiment_aggregate",
    "sentiment_mentions",
    "size_factor",
    "is_buy",
    "atr_elevated",
    "atr_compressed",
    "atr_normal",
    "trend_trending",
    "trend_ranging",
    "anomaly_flag",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
)

_ATR_REGIMES = frozenset({"elevated", "compressed", "normal"})


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _parse_entry_ts(entry_ts: str | int | float | None) -> datetime | None:
    if entry_ts is None:
        return None
    if isinstance(entry_ts, (int, float)):
        try:
            ts = float(entry_ts)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(entry_ts).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            ts = float(text)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def resolve_entry_ts(
    insight: dict | None = None,
    *,
    bar_time: str | int | float | None = None,
    entry_ts: str | int | float | None = None,
) -> str | None:
    """Best-effort entry timestamp for cyclical time features."""
    if entry_ts is not None:
        return str(entry_ts)
    data = insight or {}
    for key in ("bar_time", "generated_at", "timestamp", "as_of", "time"):
        val = data.get(key)
        if val is not None and str(val).strip():
            return str(val)
    if bar_time is not None:
        return str(bar_time)
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _cyclical(value: float, period: float) -> tuple[float, float]:
    if period <= 0:
        return 0.0, 0.0
    angle = 2.0 * math.pi * value / period
    return math.sin(angle), math.cos(angle)


def insight_to_features(
    insight: dict | None,
    *,
    symbol: str = "",
    side: str = "BUY",
    timeframe: str = "1m",
    entry_ts: str | None = None,
) -> dict[str, float]:
    """Extract entry-time features from insight snapshot (no post-entry leakage)."""
    insight = insight or {}
    sub = insight.get("sub_reports") or {}
    trend = sub.get("trend") or {}
    momentum = sub.get("momentum") or {}
    volume = sub.get("volume") or {}
    sentiment = sub.get("sentiment") or {}
    risk = sub.get("risk") or {}
    anomaly = sub.get("anomaly") or {}

    score = _safe_int(insight.get("score"))
    confidence = _safe_float(insight.get("confidence"))
    atr_regime = str(risk.get("atr_regime") or "unknown").lower()
    trend_regime = str(trend.get("trend_regime") or "unknown").lower()

    dt = _parse_entry_ts(entry_ts)
    hour = dt.hour if dt else 12
    dow = dt.weekday() if dt else 2
    hour_sin, hour_cos = _cyclical(hour, 24.0)
    dow_sin, dow_cos = _cyclical(dow, 7.0)

    side_u = str(side or "BUY").upper()
    return {
        "score": float(score),
        "confidence": confidence,
        "abs_score": float(abs(score)),
        "trend_score": float(_safe_int(trend.get("score"))),
        "momentum_score": float(_safe_int(momentum.get("score"))),
        "volume_score": float(_safe_int(volume.get("score"))),
        "sentiment_domain_score": float(_safe_int(sentiment.get("score"))),
        "sentiment_aggregate": _safe_float(sentiment.get("aggregate_score")),
        "sentiment_mentions": float(_safe_int(sentiment.get("mention_count"))),
        "size_factor": _safe_float(risk.get("suggested_size_factor"), 1.0),
        "is_buy": 1.0 if side_u == "BUY" else 0.0,
        "atr_elevated": 1.0 if atr_regime == "elevated" else 0.0,
        "atr_compressed": 1.0 if atr_regime == "compressed" else 0.0,
        "atr_normal": 1.0 if atr_regime in ("normal", "unknown") else 0.0,
        "trend_trending": 1.0 if trend_regime == "trending" else 0.0,
        "trend_ranging": 1.0 if trend_regime == "ranging" else 0.0,
        "anomaly_flag": 1.0 if anomaly.get("is_anomaly") else 0.0,
        "hour_sin": hour_sin,
        "hour_cos": hour_cos,
        "dow_sin": dow_sin,
        "dow_cos": dow_cos,
        # metadata for debugging (not in FEATURE_NAMES)
        "_symbol": symbol,
        "_timeframe": timeframe,
        "_score_bucket": score_bucket(score),
        "_confidence_bucket": confidence_bucket(confidence),
    }


def features_to_vector(features: dict[str, float]) -> np.ndarray:
    return np.array([float(features.get(name, 0.0)) for name in FEATURE_NAMES], dtype=np.float64)


def build_meta_label_dataset(
    bot_id: str,
    *,
    limit: int = 5000,
) -> dict[str, Any]:
    """Build labeled rows from closed bot trades."""
    trades = fetch_trades_for_calibration(bot_id=bot_id, limit=limit)
    tf_map = fetch_bot_timeframes([bot_id])
    entry_snapshots: dict[int, dict] = {}
    for row in trades:
        if row.get("is_exit"):
            continue
        rid = row.get("id")
        snap = row.get("insight_snapshot")
        if rid is not None and isinstance(snap, dict):
            entry_snapshots[int(rid)] = snap

    closed = pair_closed_trades(trades, bot_timeframes=tf_map)
    rows: list[dict[str, Any]] = []
    skipped = 0

    for trade in closed:
        snap = entry_snapshots.get(int(trade.entry_id)) if trade.entry_id else None
        if not snap and trade.score is None and trade.confidence is None:
            skipped += 1
            continue
        if not snap:
            snap = {
                "score": trade.score,
                "confidence": trade.confidence,
                "sub_reports": {
                    "trend": {"score": trade.trend_score},
                    "momentum": {"score": trade.momentum_score},
                    "risk": {"atr_regime": trade.atr_regime or "unknown"},
                },
            }
        feat = insight_to_features(
            snap,
            symbol=trade.symbol,
            side=trade.side,
            timeframe=trade.timeframe,
            entry_ts=trade.entry_ts,
        )
        rows.append({
            "features": feat,
            "vector": features_to_vector(feat).tolist(),
            "win": bool(trade.win),
            "pnl": trade.pnl,
            "entry_ts": trade.entry_ts,
        })

    rows.sort(key=lambda r: r.get("entry_ts") or "")
    return {
        "bot_id": bot_id,
        "rows": rows,
        "sample_count": len(rows),
        "skipped": skipped,
        "with_snapshot": len(rows) - skipped,
    }


def _bot_model_dir(bot_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(bot_id))
    return os.path.join(META_LABEL_MODEL_DIR, safe)


def _metadata_path(bot_id: str) -> str:
    return os.path.join(_bot_model_dir(bot_id), "metadata.json")


def _model_path(bot_id: str) -> str:
    return os.path.join(_bot_model_dir(bot_id), "model.joblib")


def _load_sklearn():
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.metrics import log_loss, roc_auc_score
        import joblib
    except ImportError as exc:
        raise RuntimeError(
            "scikit-learn is required for meta-label models (pip install scikit-learn)"
        ) from exc
    return HistGradientBoostingClassifier, roc_auc_score, log_loss, joblib


def train_model_from_rows(
    rows: list[dict[str, Any]],
    *,
    min_samples: int = 20,
    val_fraction: float = 0.2,
    risk_adjusted_labels: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Train HistGradientBoosting on pre-built feature rows (in-memory).

    If risk_adjusted_labels=True, uses PnL/risk ratio to create labels:
    trades with ratio > 0 are wins, ratio <= 0 are losses.  This prevents
    $1 wins on $500 risk from being treated the same as $500 wins.
    """
    n = len(rows)
    min_samples = int(min_samples)
    if n < min_samples:
        return {
            "ok": False,
            "error": f"insufficient samples ({n} < {min_samples})",
            "sample_count": n,
        }

    HistGBC, roc_auc_score, log_loss_fn, _joblib = _load_sklearn()
    X = np.vstack([features_to_vector(r["features"]) for r in rows])

    # Risk-adjusted or binary labels
    if risk_adjusted_labels:
        y = np.array(
            [1 if (r.get("pnl") or 0) > 0 else 0 for r in rows],
            dtype=np.int32,
        )
        # Weight by magnitude: bigger PnL trades matter more
        pnl_abs = np.array([abs(r.get("pnl") or 0) for r in rows], dtype=np.float64)
        pnl_max = pnl_abs.max() if pnl_abs.max() > 0 else 1.0
        sample_weights = 0.5 + 0.5 * (pnl_abs / pnl_max)  # range [0.5, 1.0]
    else:
        y = np.array([1 if r.get("win") else 0 for r in rows], dtype=np.int32)
        sample_weights = None

    split_idx = max(1, int(n * (1.0 - val_fraction)))
    if split_idx >= n:
        split_idx = n - 1
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]
    w_train = sample_weights[:split_idx] if sample_weights is not None else None

    if len(np.unique(y_train)) < 2:
        return {
            "ok": False,
            "error": "training set needs both wins and losses",
            "sample_count": n,
        }

    # Class-balanced weighting: prevent majority-class bias
    n_pos = int(y_train.sum())
    n_neg = int(len(y_train) - n_pos)
    if n_pos > 0 and n_neg > 0:
        class_weight_ratio = n_neg / n_pos
        # Integrate into sample weights
        if w_train is None:
            w_train = np.ones(len(y_train), dtype=np.float64)
        w_train[y_train == 1] *= class_weight_ratio

    model = HistGBC(
        max_depth=int(kwargs.get("gbm_max_depth", 5)),
        max_iter=int(kwargs.get("gbm_max_iter", 120)),
        learning_rate=float(kwargs.get("gbm_learning_rate", 0.08)),
        l2_regularization=float(kwargs.get("gbm_l2_reg", 0.0)),
        min_samples_leaf=max(2, min_samples // 15),
        random_state=42,
    )
    model.fit(X_train, y_train, sample_weight=w_train)

    metrics: dict[str, Any] = {
        "train_samples": int(len(y_train)),
        "val_samples": int(len(y_val)),
        "train_win_rate": round(float(y_train.mean()), 4) if len(y_train) else 0.0,
    }
    if len(y_val) >= 3 and len(np.unique(y_val)) >= 2:
        proba_val = model.predict_proba(X_val)[:, 1]
        try:
            metrics["val_auc"] = round(float(roc_auc_score(y_val, proba_val)), 4)
        except ValueError:
            metrics["val_auc"] = None
        try:
            metrics["val_log_loss"] = round(float(log_loss_fn(y_val, proba_val)), 4)
        except ValueError:
            metrics["val_log_loss"] = None

    # Refit on all rows for production inference (val split is metrics-only).
    # Recompute class balance from the full dataset (may differ from train split).
    full_n_pos = int(y.sum())
    full_n_neg = int(n - full_n_pos)
    full_class_ratio = full_n_neg / full_n_pos if full_n_pos > 0 and full_n_neg > 0 else 1.0
    all_weights = sample_weights if sample_weights is not None else None
    if all_weights is None and full_n_pos > 0 and full_n_neg > 0:
        all_weights = np.ones(n, dtype=np.float64)
        all_weights[y == 1] *= full_class_ratio
    elif all_weights is not None and full_n_pos > 0 and full_n_neg > 0:
        # Apply class balance on top of existing risk-adjusted weights
        all_weights = all_weights.copy()
        all_weights[y == 1] *= full_class_ratio
    model.fit(X, y, sample_weight=all_weights)
    metrics["fit_samples"] = int(n)
    metrics["class_balance"] = {
        "n_pos": full_n_pos,
        "n_neg": full_n_neg,
        "weight_ratio": round(float(full_class_ratio), 4),
    }
    metrics["risk_adjusted_labels"] = risk_adjusted_labels

    importances = getattr(model, "feature_importances_", None)
    top_features: list[dict[str, Any]] = []
    if importances is not None and len(importances) == len(FEATURE_NAMES):
        pairs = sorted(zip(FEATURE_NAMES, importances), key=lambda p: p[1], reverse=True)
        top_features = [{"name": n, "importance": round(float(v), 4)} for n, v in pairs[:8]]

    return {
        "ok": True,
        "model": model,
        "sample_count": n,
        "metrics": metrics,
        "top_features": top_features,
    }


def train_meta_label_model(
    bot_id: str,
    *,
    min_samples: int | None = None,
    val_fraction: float = 0.2,
) -> dict[str, Any]:
    """Train HistGradientBoosting classifier; persist artifact + metadata."""
    min_samples = int(min_samples if min_samples is not None else META_LABEL_MIN_TRAIN_SAMPLES)
    dataset = build_meta_label_dataset(bot_id)
    rows = dataset["rows"]

    trained = train_model_from_rows(rows, min_samples=min_samples, val_fraction=val_fraction)
    if not trained.get("ok"):
        return {**trained, "bot_id": bot_id}

    _, _, _, joblib = _load_sklearn()
    model = trained["model"]
    metrics = trained.get("metrics") or {}
    top_features = trained.get("top_features") or []

    os.makedirs(_bot_model_dir(bot_id), exist_ok=True)
    joblib.dump(model, _model_path(bot_id))

    metadata = {
        "bot_id": bot_id,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sample_count": trained.get("sample_count"),
        "metrics": metrics,
        "top_features": top_features,
    }
    with open(_metadata_path(bot_id), "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)

    get_meta_label_store().invalidate(bot_id)
    logger.info(
        "Meta-label model trained for %s (n=%d, val_auc=%s)",
        bot_id,
        trained.get("sample_count"),
        metrics.get("val_auc"),
    )
    return {"ok": True, "bot_id": bot_id, **metadata}


class MetaLabelModelStore:
    """In-memory cache of loaded GBM artifacts."""

    def __init__(self) -> None:
        self._models: dict[str, Any] = {}
        self._metadata: dict[str, dict[str, Any]] = {}
        self._mtime: dict[str, float] = {}

    def invalidate(self, bot_id: str | None = None) -> None:
        if bot_id:
            key = str(bot_id)
            self._models.pop(key, None)
            self._metadata.pop(key, None)
            self._mtime.pop(key, None)
        else:
            self._models.clear()
            self._metadata.clear()
            self._mtime.clear()

    def get_metadata(self, bot_id: str) -> dict[str, Any] | None:
        self._ensure_loaded(bot_id)
        return self._metadata.get(str(bot_id))

    def has_loaded_model(self, bot_id: str) -> bool:
        return self._ensure_loaded(bot_id) is not None

    def predict_proba(self, bot_id: str, features: dict[str, float]) -> float | None:
        session_model = get_backtest_session_model(bot_id)
        if session_model is not None:
            vec = features_to_vector(features).reshape(1, -1)
            try:
                proba = session_model.predict_proba(vec)[0, 1]
                return float(max(0.0, min(1.0, proba)))
            except Exception as exc:
                logger.warning("Backtest session meta-label predict failed: %s", exc)
                return None

        model = self._ensure_loaded(bot_id)
        if model is None:
            return None
        vec = features_to_vector(features).reshape(1, -1)
        try:
            proba = model.predict_proba(vec)[0, 1]
            return float(max(0.0, min(1.0, proba)))
        except Exception as exc:
            logger.warning("Meta-label predict failed for %s: %s", bot_id, exc)
            return None

    def _ensure_loaded(self, bot_id: str):
        key = str(bot_id)
        path = _model_path(key)
        meta_path = _metadata_path(key)
        if not os.path.isfile(path) or not os.path.isfile(meta_path):
            return None
        mtime = os.path.getmtime(path)
        if key in self._models and self._mtime.get(key) == mtime:
            return self._models[key]

        try:
            _, _, _, joblib = _load_sklearn()
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            if int(meta.get("feature_schema_version", 0)) != FEATURE_SCHEMA_VERSION:
                logger.warning("Meta-label schema mismatch for %s — retrain required", key)
                return None
            model = joblib.load(path)
        except Exception as exc:
            logger.warning("Meta-label load failed for %s: %s", key, exc)
            return None

        self._models[key] = model
        self._metadata[key] = meta
        self._mtime[key] = mtime
        return model


_store: MetaLabelModelStore | None = None
_backtest_session_models: dict[str, dict[str, Any]] = {}


def set_backtest_session_model(session_id: str, model: Any, metadata: dict | None = None) -> None:
    """Inject in-memory GBM for walk-forward backtest OOS windows."""
    _backtest_session_models[str(session_id)] = {
        "model": model,
        "metadata": metadata or {},
    }
    get_meta_label_store().invalidate(str(session_id))


def clear_backtest_session_model(session_id: str) -> None:
    _backtest_session_models.pop(str(session_id), None)
    get_meta_label_store().invalidate(str(session_id))


def get_backtest_session_model(session_id: str) -> Any | None:
    entry = _backtest_session_models.get(str(session_id))
    return entry.get("model") if entry else None


def get_meta_label_store() -> MetaLabelModelStore:
    global _store
    if _store is None:
        _store = MetaLabelModelStore()
    return _store


def predict_meta_label_prob(
    bot_id: str,
    insight: dict,
    *,
    symbol: str,
    side: str,
    timeframe: str,
    bar_time: str | int | float | None = None,
    entry_ts: str | int | float | None = None,
) -> float | None:
    ts = resolve_entry_ts(insight, bar_time=bar_time, entry_ts=entry_ts)
    features = insight_to_features(
        insight,
        symbol=symbol,
        side=side,
        timeframe=timeframe,
        entry_ts=ts,
    )
    return get_meta_label_store().predict_proba(bot_id, features)


def explain_prediction(
    bot_id: str,
    insight: dict,
    *,
    symbol: str,
    side: str,
    timeframe: str,
    bar_time: str | int | float | None = None,
    entry_ts: str | int | float | None = None,
    top_k: int = 5,
) -> dict[str, Any] | None:
    """Return feature contributions for a meta-label prediction.

    Uses feature importances × sign of feature deviation from mean as
    a lightweight SHAP approximation.  Returns the top_k contributing
    features with direction (+/−) and magnitude.

    Returns:
        {
            "prob": float,
            "contributions": [
                {"feature": str, "value": float, "contribution": float, "direction": str},
                ...
            ],
            "decision": "pass" | "block",
        }
    """
    ts = resolve_entry_ts(insight, bar_time=bar_time, entry_ts=entry_ts)
    features = insight_to_features(
        insight,
        symbol=symbol,
        side=side,
        timeframe=timeframe,
        entry_ts=ts,
    )
    store = get_meta_label_store()
    prob = store.predict_proba(bot_id, features)
    if prob is None:
        return None

    # Get model for feature importances — check session models first (backtest),
    # then the persistent store (same lookup order as predict_proba).
    model = get_backtest_session_model(bot_id)
    if model is None:
        model = store._models.get(str(bot_id))
    if model is None:
        return {"prob": prob, "contributions": [], "decision": "pass" if prob >= 0.5 else "block"}

    importances = getattr(model, "feature_importances_", None)
    if importances is None or len(importances) != len(FEATURE_NAMES):
        return {"prob": prob, "contributions": [], "decision": "pass" if prob >= 0.5 else "block"}

    vec = features_to_vector(features)
    contributions: list[dict[str, Any]] = []
    for i, (name, imp) in enumerate(zip(FEATURE_NAMES, importances)):
        val = float(vec[i])
        # Positive feature values with high importance → bullish contribution
        # Negative → bearish.  Scale by importance.
        contrib = val * float(imp)
        direction = "bullish" if contrib > 0 else "bearish" if contrib < 0 else "neutral"
        contributions.append({
            "feature": name,
            "value": round(val, 4),
            "contribution": round(contrib, 4),
            "direction": direction,
        })

    contributions.sort(key=lambda c: abs(c["contribution"]), reverse=True)

    return {
        "prob": round(prob, 4),
        "contributions": contributions[:top_k],
        "decision": "pass" if prob >= 0.5 else "block",
    }


def get_meta_label_status(bot_id: str) -> dict[str, Any]:
    store = get_meta_label_store()
    meta = store.get_metadata(bot_id)
    if not meta:
        path = _metadata_path(bot_id)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    meta = json.load(fh)
            except Exception:
                meta = None
    dataset = build_meta_label_dataset(bot_id)
    load_error: str | None = None
    if meta and not store.has_loaded_model(bot_id):
        load_error = "artifact present but model failed to load — retrain required"
    return {
        "bot_id": bot_id,
        "model_loaded": store.has_loaded_model(bot_id),
        "metadata": meta,
        "load_error": load_error,
        "dataset": {
            "sample_count": dataset["sample_count"],
            "skipped": dataset["skipped"],
        },
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
    }


def _load_bot_gate_config(bot_id: str) -> dict[str, Any]:
    from app.db.connection import get_connection
    from app.services.bots.indicators import merge_strategy_config

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT strategy, config FROM bots WHERE id = ?",
        (bot_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {}
    if isinstance(row, dict):
        strategy = row.get("strategy") or "CHART_AGENT"
        cfg_raw = row.get("config")
    else:
        strategy = row[0]
        cfg_raw = row[1]
    cfg = cfg_raw
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg) if cfg else {}
        except json.JSONDecodeError:
            cfg = {}
    return merge_strategy_config(str(strategy), cfg or {})


def refresh_meta_label_models(
    bot_ids: list[str] | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Retrain GBM models for bots configured for gbm/hybrid mode."""
    from app.services.bots.calibration import list_active_bot_ids_for_calibration

    ids = bot_ids or list_active_bot_ids_for_calibration()
    trained = 0
    skipped = 0
    errors: list[str] = []

    for bot_id in ids:
        cfg = _load_bot_gate_config(bot_id)
        mode = str(cfg.get("meta_label_model_mode") or "wilson").lower()
        if mode not in ("gbm", "hybrid") and not cfg.get("meta_label_model_enabled"):
            skipped += 1
            continue
        if not cfg.get("calibration_gate_enabled") and not force:
            skipped += 1
            continue
        try:
            min_n = int(cfg.get("meta_label_min_train_samples") or META_LABEL_MIN_TRAIN_SAMPLES)
            result = train_meta_label_model(bot_id, min_samples=min_n)
            if result.get("ok"):
                trained += 1
            else:
                skipped += 1
        except Exception as exc:
            errors.append(f"{bot_id}: {exc}")
            logger.warning("Meta-label retrain failed for %s: %s", bot_id, exc)

    return {"bots": len(ids), "trained": trained, "skipped": skipped, "errors": errors}


def check_gbm_meta_label_gate(
    insight: dict,
    cfg: dict,
    *,
    symbol: str,
    timeframe: str,
    signal: str,
    bot_id: str,
    prob: float | None = None,
) -> str | None:
    """GBM path: block when P(win) below threshold."""
    try:
        min_prob = float(cfg.get("meta_label_min_prob", 0.52))
    except (TypeError, ValueError):
        min_prob = 0.52
    min_prob = max(0.0, min(1.0, min_prob))

    if prob is None:
        prob = predict_meta_label_prob(
            bot_id,
            insight,
            symbol=symbol,
            side=signal,
            timeframe=timeframe,
        )
    if prob is None:
        return None

    from app.observability.metrics import inc

    shadow = bool(cfg.get("meta_label_shadow_mode"))
    if prob < min_prob:
        reason = (
            f"meta-label gate: P(win) {prob:.2%} below {min_prob:.2%}"
        )
        inc("meta_label_shadow_would_block_total" if shadow else "meta_label_blocked_total")
        if shadow:
            logger.info(
                "Meta-label shadow block %s %s %s prob=%.3f",
                bot_id,
                symbol,
                signal,
                prob,
            )
            return None
        return reason

    inc("meta_label_passed_total")
    return None
