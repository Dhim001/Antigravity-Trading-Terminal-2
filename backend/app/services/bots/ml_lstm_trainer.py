"""LSTM Direction Classifier — training pipeline with ONNX export.

Trains a 2-layer LSTM on sliding windows of bar features to predict
BUY/SELL/NONE via triple-barrier labels. Training uses CUDA when available;
exports ONNX for CPU ``onnxruntime`` inference (no PyTorch at runtime).

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


def _model_dir(symbol: str, timeframe: str | None = None) -> str:
    from app.services.bots.ml_model_artifacts import model_storage_key

    return os.path.join(LSTM_MODEL_DIR, model_storage_key(symbol, timeframe))


def _onnx_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "lstm_direction.onnx")


def _metadata_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "metadata.json")


def _scaler_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "scaler.json")


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


def save_scaler(
    symbol: str,
    scaler: dict[str, list[float]],
    *,
    timeframe: str | None = None,
) -> None:
    os.makedirs(_model_dir(symbol, timeframe), exist_ok=True)
    with open(_scaler_path(symbol, timeframe), "w", encoding="utf-8") as fh:
        json.dump(scaler, fh, indent=2)


def load_scaler(
    symbol: str, *, model_dir: str | None = None, timeframe: str | None = None
) -> dict[str, list[float]] | None:
    path = (
        os.path.join(model_dir, "scaler.json")
        if model_dir
        else _scaler_path(symbol, timeframe)
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
    epochs: int = 100,
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

    raw_cfg = dict(config or {})
    cfg = merge_strategy_config("LSTM_DIRECTION", raw_cfg)
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe

    tf = normalize_model_timeframe(cfg.get("timeframe") or raw_cfg.get("timeframe"))
    cfg["timeframe"] = tf
    epochs = int(cfg.get("epochs", epochs))
    lookback = int(cfg.get("lookback", 90))
    # Interactive / walk-forward validation uses short fold windows — relax the
    # production sample floor so folds can actually train (unless caller overrides).
    if bool(cfg.get("_wf_mode")) and "min_train_samples" not in raw_cfg:
        min_samples = int(cfg.get("wf_min_train_samples", 150))
    else:
        min_samples = int(cfg.get("min_train_samples", 500))
    atr_mult = float(cfg.get("triple_barrier_atr_mult", 2.0))
    max_bars = int(cfg.get("triple_barrier_max_bars", 30))
    val_fraction = float(cfg.get("val_fraction", 0.2))
    hidden_dim = int(cfg.get("hidden_dim", 128))
    num_layers = int(cfg.get("num_layers", 2))
    lr = float(cfg.get("learning_rate", 0.001))
    from app.services.bots.ml_torch_device import (
        cap_wf_epochs,
        cpu_tensor,
        device_info,
        ensure_cuda_ready,
        resolve_torch_device,
        resolve_wf_torch_device,
        suggest_batch_size,
    )
    from app.services.bots.ml_job_progress import (
        cancelled_train_result,
        ml_cancel_requested,
        progress_path_from_config,
        write_ml_progress,
    )

    # WF folds: CUDA when available; short epoch budget (not full Lab train).
    if bool(cfg.get("_wf_mode")):
        epochs = cap_wf_epochs(epochs, cfg, default=12)
        device = resolve_wf_torch_device(cfg)
    else:
        device = resolve_torch_device(cfg)
    batch_size = suggest_batch_size(
        cfg, 128 if getattr(device, "type", None) == "cuda" else 64, device=device,
    )
    progress_path = progress_path_from_config(cfg)
    write_ml_progress(progress_path, pct=8, phase="device", detail=str(device))
    ensure_cuda_ready(device)

    min_candles = lookback + 20 + max_bars + min_samples
    if len(candles) < min_candles:
        return {
            "ok": False,
            "error": f"insufficient candles ({len(candles)} < {min_candles})",
            "symbol": symbol,
        }

    # Step 1: Label candles
    write_ml_progress(progress_path, pct=10, phase="labels", detail="triple-barrier")
    labels = label_triple_barrier(
        candles,
        atr_mult_upper=atr_mult,
        atr_mult_lower=atr_mult,
        max_holding_bars=max_bars,
    )
    dist = label_distribution(labels)

    # Step 2: Build sliding window sequences
    write_ml_progress(progress_path, pct=15, phase="sequences", detail="build windows")
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

    # Step 5: Build model (CUDA when available for production trains)
    model = _build_lstm_model(
        input_dim=N_FEATURES,
        hidden_dim=hidden_dim,
        num_layers=num_layers,
        num_classes=N_CLASSES,
    ).to(device)

    # Class weights for imbalanced labels
    class_counts = np.bincount(y_train, minlength=N_CLASSES).astype(np.float32)
    class_counts = np.maximum(class_counts, 1.0)
    class_weights = (1.0 / class_counts) * class_counts.sum() / N_CLASSES
    weight_tensor = torch.tensor(class_weights, dtype=torch.float32, device=device)

    criterion = nn.CrossEntropyLoss(weight=weight_tensor)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=5, min_lr=1e-5,
    )

    # Keep full datasets on CPU — move mini-batches to GPU (avoids CUDA alloc hang/OOM).
    X_train_t = cpu_tensor(X_train, dtype=torch.float32)
    y_train_t = cpu_tensor(y_train, dtype=torch.long)
    X_val_t = cpu_tensor(X_val, dtype=torch.float32)
    y_val_t = cpu_tensor(y_val, dtype=torch.long)

    logger.info(
        "LSTM train %s @ %s on %s (hidden=%d layers=%d batch=%d epochs≤%d)",
        symbol, tf, device, hidden_dim, num_layers, batch_size, epochs,
    )
    write_ml_progress(
        progress_path, pct=20, phase="fit",
        detail=f"{device} · {len(y_train)} train / {len(y_val)} val",
    )

    # Step 6: Training loop
    best_val_loss = float("inf")
    best_state = None
    patience_counter = 0
    max_patience = 10
    loss_history: list[dict] = []

    def _batched_val_loss() -> float:
        total = 0.0
        n = 0
        for vs in range(0, len(X_val_t), batch_size):
            xb = X_val_t[vs:vs + batch_size].to(device, non_blocking=True)
            yb = y_val_t[vs:vs + batch_size].to(device, non_blocking=True)
            total += float(criterion(model(xb), yb).item()) * len(xb)
            n += len(xb)
        return total / max(1, n)

    model.train()
    for epoch in range(epochs):
        if ml_cancel_requested(progress_path):
            return cancelled_train_result(symbol, "LSTM_DIRECTION")
        # Mini-batch training
        indices = torch.randperm(len(X_train_t))
        epoch_loss = 0.0
        n_batches = 0

        for start in range(0, len(X_train_t), batch_size):
            end = min(start + batch_size, len(X_train_t))
            batch_idx = indices[start:end]
            xb = X_train_t[batch_idx].to(device, non_blocking=True)
            yb = y_train_t[batch_idx].to(device, non_blocking=True)

            optimizer.zero_grad()
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            epoch_loss += loss.item()
            n_batches += 1

        avg_train_loss = epoch_loss / max(1, n_batches)

        # Validation (batched — never forward the full val set on GPU at once)
        model.eval()
        with torch.no_grad():
            val_loss = _batched_val_loss()
        model.train()

        loss_history.append({
            "epoch": epoch + 1,
            "train_loss": round(avg_train_loss, 6),
            "val_loss": round(val_loss, 6),
        })
        write_ml_progress(
            progress_path,
            pct=min(20 + int(((epoch + 1) / max(epochs, 1)) * 70), 90),
            phase="epoch",
            detail=f"{epoch + 1}/{epochs} · val_loss={val_loss:.4f}",
        )

        scheduler.step(val_loss)

        # Early stopping
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
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
    val_logits_chunks: list = []
    with torch.no_grad():
        for vs in range(0, len(X_val_t), batch_size):
            xb = X_val_t[vs:vs + batch_size].to(device, non_blocking=True)
            val_logits_chunks.append(model(xb).detach().cpu())
        val_logits = torch.cat(val_logits_chunks, dim=0) if val_logits_chunks else torch.empty(0, N_CLASSES)
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

    train_device_meta = device_info(device)
    metrics = {
        "train_samples": int(len(y_train)),
        "val_samples": int(len(y_val)),
        "val_accuracy": round(val_acc, 4),
        "val_loss": round(best_val_loss, 4),
        "epochs_trained": epoch + 1,
        "hidden_dim": hidden_dim,
        "num_layers": num_layers,
        "lookback": lookback,
        "batch_size": batch_size,
        "train_device": train_device_meta.get("device"),
        **per_class_acc,
    }

    # Step 8: Export to ONNX (single-file; Windows-safe across walk-forward re-exports)
    os.makedirs(_model_dir(symbol, tf), exist_ok=True)
    onnx_path = _onnx_path(symbol, tf)
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
        invalidate=lambda: get_lstm_store().invalidate(symbol, timeframe=tf),
    )

    # Save scaler
    save_scaler(symbol, scaler, timeframe=tf)

    # Save metadata
    metadata = {
        "symbol": symbol,
        "timeframe": tf,
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
            "timeframe": tf,
            "train_device": train_device_meta,
        },
        "train_device": train_device_meta,
    }
    with open(_metadata_path(symbol, tf), "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)

    # Walk-forward / interactive validate sets skip_snapshot to avoid polluting
    # version history and clobbering the live champion across folds.
    skip_snapshot = bool(cfg.get("skip_snapshot", cfg.get("_wf_mode", False)))
    if not skip_snapshot:
        try:
            from app.services.bots.ml_model_artifacts import snapshot_current_version
            snap = snapshot_current_version(_model_dir(symbol, tf), strategy="LSTM_DIRECTION")
            if snap:
                metadata["version_id"] = snap.get("version_id")
                metadata["version_path"] = snap.get("path")
        except Exception:
            logger.exception("Failed to snapshot LSTM version for %s", symbol)

    logger.info(
        "LSTM signal model trained for %s @ %s (n=%d, val_acc=%.4f, epochs=%d)",
        symbol, tf, n, val_acc, epoch + 1,
    )
    return {"ok": True, "symbol": symbol, "timeframe": tf, **metadata}


# Back-compat alias for HTTP /api/v1/ml/train dispatchers
train_lstm_model = train_lstm_signal_model
