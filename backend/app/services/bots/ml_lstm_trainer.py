"""LSTM Direction Classifier — training pipeline with ONNX export.

Trains a 2-layer LSTM on sliding windows of bar features to predict
BUY/SELL/NONE via triple-barrier labels.  Exports to ONNX for lightweight
CPU inference (no PyTorch needed at runtime).

Dependencies (optional — strategy degrades gracefully if absent):
    pip install torch>=2.3.0 onnxruntime>=1.18.0
"""

from __future__ import annotations

import json
import logging
import math
import os
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
from app.services.bots.ml_triple_barrier import label_distribution, label_triple_barrier

logger = logging.getLogger(__name__)

LSTM_MODEL_DIR = os.path.join(BASE_DIR, "data", "lstm_signal_models")
N_FEATURES = len(SIGNAL_FEATURE_NAMES)
N_CLASSES = 3  # BUY=0, NONE=1, SELL=2
LABEL_MAP = {1: 0, 0: 1, -1: 2}
REVERSE_MAP = {0: "BUY", 1: "NONE", 2: "SELL"}


def _model_dir(symbol: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(symbol).upper())
    return os.path.join(LSTM_MODEL_DIR, safe)


def _onnx_path(symbol: str) -> str:
    return os.path.join(_model_dir(symbol), "lstm_direction.onnx")


def _metadata_path(symbol: str) -> str:
    return os.path.join(_model_dir(symbol), "metadata.json")


def _scaler_path(symbol: str) -> str:
    return os.path.join(_model_dir(symbol), "scaler.json")


# ── Feature scaling ──────────────────────────────────────────────────────


def compute_scaler(sequences: np.ndarray) -> dict[str, list[float]]:
    """Compute per-feature mean and std from training sequences.

    Parameters
    ----------
    sequences : np.ndarray of shape (N, seq_len, n_features)

    Returns
    -------
    dict with "mean" and "std" lists, each of length n_features.
    """
    # Flatten to (N*seq_len, n_features)
    flat = sequences.reshape(-1, sequences.shape[-1])
    mean = flat.mean(axis=0).tolist()
    std = flat.std(axis=0).tolist()
    # Prevent division by zero
    std = [s if s > 1e-8 else 1.0 for s in std]
    return {"mean": mean, "std": std}


def apply_scaler(sequences: np.ndarray, scaler: dict[str, list[float]]) -> np.ndarray:
    """Z-score normalize sequences in-place."""
    mean = np.array(scaler["mean"], dtype=np.float32)
    std = np.array(scaler["std"], dtype=np.float32)
    return (sequences - mean) / std


def save_scaler(symbol: str, scaler: dict[str, list[float]]) -> None:
    os.makedirs(_model_dir(symbol), exist_ok=True)
    with open(_scaler_path(symbol), "w", encoding="utf-8") as fh:
        json.dump(scaler, fh, indent=2)


def load_scaler(
    symbol: str, *, model_dir: str | None = None
) -> dict[str, list[float]] | None:
    path = (
        os.path.join(model_dir, "scaler.json")
        if model_dir
        else _scaler_path(symbol)
    )
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


# ── PyTorch model definition ─────────────────────────────────────────────


def _get_torch():
    """Lazy import torch — returns (torch, nn) or raises RuntimeError."""
    try:
        import torch
        import torch.nn as nn
        return torch, nn
    except ImportError as exc:
        raise RuntimeError(
            "PyTorch is required for LSTM training (pip install torch>=2.3.0)"
        ) from exc


def _build_lstm_model(input_dim: int = N_FEATURES, hidden_dim: int = 64,
                      num_layers: int = 2, num_classes: int = N_CLASSES):
    """Build the LSTM model using PyTorch."""
    torch, nn = _get_torch()

    class LstmDirectionNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(
                input_dim, hidden_dim, num_layers,
                batch_first=True, dropout=0.2 if num_layers > 1 else 0.0,
            )
            self.dropout = nn.Dropout(0.3)
            self.fc = nn.Linear(hidden_dim, num_classes)

        def forward(self, x):
            # x: (batch, seq_len, input_dim)
            _, (h_n, _) = self.lstm(x)
            # h_n: (num_layers, batch, hidden_dim) — take last layer
            out = self.dropout(h_n[-1])
            return self.fc(out)

    return LstmDirectionNet()


# ── Sequence building ────────────────────────────────────────────────────


def build_sequences(
    candles: list[dict],
    labels: list[dict],
    *,
    lookback: int = 60,
    max_holding_bars: int = 30,
) -> tuple[np.ndarray, np.ndarray]:
    """Build (X, y) sliding window sequences from labelled candles.

    Returns
    -------
    X : np.ndarray of shape (N, lookback, N_FEATURES)
    y : np.ndarray of shape (N,) with values in {0, 1, 2}
    """
    n = len(candles)
    feature_lookback = 20  # for rolling features inside bar_to_signal_features

    sequences_x: list[np.ndarray] = []
    sequences_y: list[int] = []

    for i in range(lookback + feature_lookback, n - max_holding_bars):
        label_info = labels[i]
        if label_info.get("barrier_hit") == "invalid":
            continue

        # Build the sequence: features for bars [i-lookback .. i-1, i]
        window_vectors: list[np.ndarray] = []
        for j in range(i - lookback + 1, i + 1):
            candle = candles[j]
            lb_start = max(0, j - feature_lookback)
            lb_rows = candles[lb_start:j]
            features = bar_to_signal_features(candle, lookback_rows=lb_rows)
            window_vectors.append(signal_features_to_vector(features))

        sequences_x.append(np.stack(window_vectors))  # (lookback, N_FEATURES)
        sequences_y.append(LABEL_MAP[label_info["label"]])

    if not sequences_x:
        return np.array([]), np.array([])

    X = np.stack(sequences_x).astype(np.float32)  # (N, lookback, N_FEATURES)
    y = np.array(sequences_y, dtype=np.int64)
    return X, y


# ── Training pipeline ────────────────────────────────────────────────────


def train_lstm_signal_model(
    symbol: str,
    candles: list[dict],
    *,
    config: dict | None = None,
    epochs: int = 50,
) -> dict[str, Any]:
    """Train an LSTM direction classifier and export to ONNX.

    Parameters
    ----------
    symbol : str
        Trading symbol (e.g. "BTCUSDT").
    candles : list[dict]
        OHLCV bars with indicators computed. Sorted oldest-first.
    config : dict, optional
        Strategy config overrides.
    epochs : int
        Training epochs.

    Returns
    -------
    dict with ``ok``, ``metrics``, ``label_distribution``, etc.
    """
    torch, nn = _get_torch()

    cfg = merge_strategy_config("LSTM_DIRECTION", config or {})
    lookback = int(cfg.get("lookback", 60))
    min_samples = int(cfg.get("min_train_samples", 500))
    atr_mult = float(cfg.get("triple_barrier_atr_mult", 2.0))
    max_bars = int(cfg.get("triple_barrier_max_bars", 30))
    val_fraction = float(cfg.get("val_fraction", 0.2))
    hidden_dim = int(cfg.get("hidden_dim", 64))
    num_layers = int(cfg.get("num_layers", 2))
    lr = float(cfg.get("learning_rate", 0.001))
    batch_size = int(cfg.get("batch_size", 64))

    min_candles = lookback + 20 + max_bars + min_samples
    if len(candles) < min_candles:
        return {
            "ok": False,
            "error": f"insufficient candles ({len(candles)} < {min_candles})",
            "symbol": symbol,
        }

    # Step 1: Label candles
    labels = label_triple_barrier(
        candles,
        atr_mult_upper=atr_mult,
        atr_mult_lower=atr_mult,
        max_holding_bars=max_bars,
    )
    dist = label_distribution(labels)

    # Step 2: Build sliding window sequences
    X, y = build_sequences(candles, labels, lookback=lookback, max_holding_bars=max_bars)
    n = len(y)
    if n < min_samples:
        return {
            "ok": False,
            "error": f"insufficient sequences ({n} < {min_samples})",
            "symbol": symbol,
            "label_distribution": dist,
        }

    # Step 3: Time-ordered train/val split
    split_idx = max(1, int(n * (1.0 - val_fraction)))
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

    n_classes = len(np.unique(y_train))
    if n_classes < 2:
        return {
            "ok": False,
            "error": f"training set needs ≥2 classes, got {n_classes}",
            "symbol": symbol,
            "label_distribution": dist,
        }

    # Step 4: Compute and apply feature scaling
    scaler = compute_scaler(X_train)
    X_train = apply_scaler(X_train, scaler)
    X_val = apply_scaler(X_val, scaler)

    # Step 5: Build model
    model = _build_lstm_model(
        input_dim=N_FEATURES,
        hidden_dim=hidden_dim,
        num_layers=num_layers,
        num_classes=N_CLASSES,
    )

    # Class weights for imbalanced labels
    class_counts = np.bincount(y_train, minlength=N_CLASSES).astype(np.float32)
    class_counts = np.maximum(class_counts, 1.0)
    class_weights = (1.0 / class_counts) * class_counts.sum() / N_CLASSES
    weight_tensor = torch.tensor(class_weights, dtype=torch.float32)

    criterion = nn.CrossEntropyLoss(weight=weight_tensor)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=5, min_lr=1e-5,
    )

    # Convert to tensors
    X_train_t = torch.tensor(X_train, dtype=torch.float32)
    y_train_t = torch.tensor(y_train, dtype=torch.long)
    X_val_t = torch.tensor(X_val, dtype=torch.float32)
    y_val_t = torch.tensor(y_val, dtype=torch.long)

    # Step 6: Training loop
    best_val_loss = float("inf")
    best_state = None
    patience_counter = 0
    max_patience = 10
    loss_history: list[dict] = []

    model.train()
    for epoch in range(epochs):
        # Mini-batch training
        indices = torch.randperm(len(X_train_t))
        epoch_loss = 0.0
        n_batches = 0

        for start in range(0, len(X_train_t), batch_size):
            end = min(start + batch_size, len(X_train_t))
            batch_idx = indices[start:end]
            xb = X_train_t[batch_idx]
            yb = y_train_t[batch_idx]

            optimizer.zero_grad()
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            epoch_loss += loss.item()
            n_batches += 1

        avg_train_loss = epoch_loss / max(1, n_batches)

        # Validation
        model.eval()
        with torch.no_grad():
            val_logits = model(X_val_t)
            val_loss = criterion(val_logits, y_val_t).item()
        model.train()

        loss_history.append({
            "epoch": epoch + 1,
            "train_loss": round(avg_train_loss, 6),
            "val_loss": round(val_loss, 6),
        })

        scheduler.step(val_loss)

        # Early stopping
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= max_patience:
                logger.info("Early stopping at epoch %d", epoch + 1)
                break

    # Load best weights
    if best_state is not None:
        model.load_state_dict(best_state)

    # Step 7: Validation metrics
    model.eval()
    with torch.no_grad():
        val_logits = model(X_val_t)
        val_proba = torch.softmax(val_logits, dim=1).numpy()
        val_preds = val_logits.argmax(dim=1).numpy()

    val_acc = float((val_preds == y_val).mean())
    per_class_acc = {}
    for cls_idx, cls_name in REVERSE_MAP.items():
        mask = y_val == cls_idx
        if mask.sum() > 0:
            per_class_acc[f"val_acc_{cls_name.lower()}"] = round(
                float((val_preds[mask] == cls_idx).mean()), 4
            )

    metrics = {
        "train_samples": int(len(y_train)),
        "val_samples": int(len(y_val)),
        "val_accuracy": round(val_acc, 4),
        "val_loss": round(best_val_loss, 4),
        "epochs_trained": epoch + 1,
        "hidden_dim": hidden_dim,
        "num_layers": num_layers,
        "lookback": lookback,
        **per_class_acc,
    }

    # Step 8: Export to ONNX (single-file; Windows-safe across walk-forward re-exports)
    os.makedirs(_model_dir(symbol), exist_ok=True)
    onnx_path = _onnx_path(symbol)
    from app.services.bots.ml_model_artifacts import export_onnx_single_file
    from app.services.bots.strategies_lstm import get_lstm_store

    export_onnx_single_file(
        model,
        torch.randn(1, lookback, N_FEATURES),
        onnx_path,
        input_names=["input"],
        output_names=["logits"],
        dynamic_axes={
            "input": {0: "batch_size"},
            "logits": {0: "batch_size"},
        },
        opset_version=17,
        invalidate=lambda: get_lstm_store().invalidate(symbol),
    )

    # Save scaler
    save_scaler(symbol, scaler)

    # Save metadata
    metadata = {
        "symbol": symbol,
        "model_type": "lstm_direction",
        "feature_schema_version": SIGNAL_FEATURE_VERSION,
        "feature_names": list(SIGNAL_FEATURE_NAMES),
        "label_map": {str(k): v for k, v in LABEL_MAP.items()},
        "reverse_map": {str(k): v for k, v in REVERSE_MAP.items()},
        "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sample_count": n,
        "label_distribution": dist,
        "metrics": metrics,
        "loss_history": loss_history,
        "config": {
            "lookback": lookback,
            "hidden_dim": hidden_dim,
            "num_layers": num_layers,
            "atr_mult": atr_mult,
            "max_holding_bars": max_bars,
        },
    }
    with open(_metadata_path(symbol), "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)

    try:
        from app.services.bots.ml_model_artifacts import snapshot_current_version
        snap = snapshot_current_version(_model_dir(symbol), strategy="LSTM_DIRECTION")
        if snap:
            metadata["version_id"] = snap.get("version_id")
            metadata["version_path"] = snap.get("path")
    except Exception:
        logger.exception("Failed to snapshot LSTM version for %s", symbol)

    logger.info(
        "LSTM signal model trained for %s (n=%d, val_acc=%.4f, epochs=%d)",
        symbol, n, val_acc, epoch + 1,
    )
    return {"ok": True, "symbol": symbol, **metadata}


# Back-compat alias for HTTP /api/v1/ml/train dispatchers
train_lstm_model = train_lstm_signal_model
