"""TCN (Temporal Convolutional Network) Multi-Horizon Forecaster.

Dilated causal CNN that predicts 5-bar, 15-bar, and 60-bar returns from
120-bar feature sequences.  Training uses PyTorch; inference via ONNX.

Architecture:
    4 residual blocks of CausalConv1d with exponentially increasing dilation
    (1, 2, 4, 8) → final linear head outputs 3 return predictions.
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

logger = logging.getLogger(__name__)

TCN_MODEL_DIR = os.path.join(BASE_DIR, "data", "tcn_signal_models")
N_FEATURES = len(SIGNAL_FEATURE_NAMES)
N_HORIZONS = 3  # 5-bar, 15-bar, 60-bar returns


def _model_dir(symbol: str, timeframe: str | None = None) -> str:
    from app.services.bots.ml_model_artifacts import model_storage_key

    return os.path.join(TCN_MODEL_DIR, model_storage_key(symbol, timeframe))


def _onnx_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "tcn_multi_horizon.onnx")


def _metadata_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "metadata.json")


def _scaler_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "scaler.json")


def _get_torch():
    try:
        import torch
        import torch.nn as nn
        return torch, nn
    except ImportError as exc:
        raise RuntimeError("PyTorch required for TCN training") from exc


# ── TCN Model ─────────────────────────────────────────────────────────────


def _build_tcn(input_dim: int = N_FEATURES, hidden_dim: int = 128,
               num_blocks: int = 6, kernel_size: int = 3,
               n_outputs: int = N_HORIZONS):
    """Build a dilated causal temporal convolutional network."""
    torch, nn = _get_torch()

    class CausalConv1dBlock(nn.Module):
        def __init__(self, in_ch, out_ch, k, dilation):
            super().__init__()
            padding = (k - 1) * dilation  # causal padding
            self.conv = nn.Conv1d(in_ch, out_ch, k, dilation=dilation, padding=padding)
            self.bn = nn.BatchNorm1d(out_ch)
            self.relu = nn.ReLU()
            self.dropout = nn.Dropout(0.2)
            self.residual = nn.Conv1d(in_ch, out_ch, 1) if in_ch != out_ch else nn.Identity()
            self._padding = padding

        def forward(self, x):
            # x: (batch, channels, seq_len)
            out = self.conv(x)
            if self._padding > 0:
                out = out[:, :, :-self._padding]  # remove future padding (causal)
            out = self.bn(out)
            out = self.relu(out)
            out = self.dropout(out)
            return out + self.residual(x)

    class TcnMultiHorizon(nn.Module):
        def __init__(self):
            super().__init__()
            blocks = []
            in_ch = input_dim
            for i in range(num_blocks):
                dilation = 2 ** i
                blocks.append(CausalConv1dBlock(in_ch, hidden_dim, kernel_size, dilation))
                in_ch = hidden_dim
            self.tcn = nn.Sequential(*blocks)
            self.head = nn.Linear(hidden_dim, n_outputs)

        def forward(self, x):
            # x: (batch, seq_len, input_dim)
            x = x.permute(0, 2, 1)  # → (batch, input_dim, seq_len)
            out = self.tcn(x)        # → (batch, hidden_dim, seq_len)
            last = out[:, :, -1]     # take last timestep
            return self.head(last)   # → (batch, n_outputs)

    return TcnMultiHorizon()


# ── Training data ─────────────────────────────────────────────────────────


def _compute_forward_returns(closes: list[float], idx: int) -> tuple[float, float, float] | None:
    """Compute 5/15/60-bar forward returns from index."""
    n = len(closes)
    if idx + 60 >= n or closes[idx] <= 0:
        return None
    ret_5 = (closes[idx + 5] - closes[idx]) / closes[idx] if idx + 5 < n else None
    ret_15 = (closes[idx + 15] - closes[idx]) / closes[idx] if idx + 15 < n else None
    ret_60 = (closes[idx + 60] - closes[idx]) / closes[idx] if idx + 60 < n else None
    if ret_5 is None or ret_15 is None or ret_60 is None:
        return None
    return ret_5, ret_15, ret_60


def build_tcn_sequences(
    candles: list[dict],
    *,
    lookback: int = 120,
) -> tuple[np.ndarray, np.ndarray]:
    """Build (X, y) sequences for TCN training.

    X: (N, lookback, N_FEATURES)
    y: (N, 3) — forward returns at 5, 15, 60 bars
    """
    n = len(candles)
    feature_lb = 20
    closes = [float(c.get("close") or 0) for c in candles]

    sequences_x: list[np.ndarray] = []
    sequences_y: list[np.ndarray] = []

    for i in range(lookback + feature_lb, n - 60):
        returns = _compute_forward_returns(closes, i)
        if returns is None:
            continue

        window: list[np.ndarray] = []
        for j in range(i - lookback + 1, i + 1):
            lb_start = max(0, j - feature_lb)
            features = bar_to_signal_features(candles[j], lookback_rows=candles[lb_start:j])
            window.append(signal_features_to_vector(features))

        sequences_x.append(np.stack(window))
        sequences_y.append(np.array(returns, dtype=np.float32))

    if not sequences_x:
        return np.array([]), np.array([])

    return np.stack(sequences_x).astype(np.float32), np.stack(sequences_y)


# ── Training pipeline ────────────────────────────────────────────────────


def train_tcn_model(
    symbol: str,
    candles: list[dict],
    *,
    config: dict | None = None,
    epochs: int = 100,
) -> dict[str, Any]:
    """Train a TCN multi-horizon forecaster."""
    torch, nn = _get_torch()

    raw_cfg = dict(config or {})
    cfg = merge_strategy_config("TCN_MULTI_HORIZON", raw_cfg)
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe

    tf = normalize_model_timeframe(cfg.get("timeframe") or raw_cfg.get("timeframe"))
    cfg["timeframe"] = tf
    epochs = int(cfg.get("epochs", epochs))
    lookback = int(cfg.get("lookback", 120))
    hidden_dim = int(cfg.get("hidden_dim", 128))
    num_blocks = int(cfg.get("num_blocks", 6))
    min_samples = int(cfg.get("min_train_samples", 300))
    lr = float(cfg.get("learning_rate", 0.001))
    val_fraction = float(cfg.get("val_fraction", 0.2))
    from app.services.bots.ml_torch_device import (
        cap_wf_epochs,
        cpu_tensor,
        device_info,
        ensure_cuda_ready,
        resolve_torch_device,
        resolve_wf_torch_device,
        suggest_batch_size,
    )

    if bool(cfg.get("_wf_mode")):
        epochs = cap_wf_epochs(epochs, cfg, default=10)
        device = resolve_wf_torch_device(cfg)
    else:
        device = resolve_torch_device(cfg)
    batch_size = suggest_batch_size(cfg, 64, device=device)
    ensure_cuda_ready(device)

    if len(candles) < lookback + 120:
        return {"ok": False, "error": f"insufficient candles ({len(candles)})", "symbol": symbol}

    # Build sequences
    X, y = build_tcn_sequences(candles, lookback=lookback)
    n = len(y)
    if n < min_samples:
        return {"ok": False, "error": f"insufficient sequences ({n} < {min_samples})", "symbol": symbol}

    # Normalize features
    flat = X.reshape(-1, N_FEATURES)
    feat_mean = flat.mean(axis=0)
    feat_std = flat.std(axis=0)
    feat_std = np.where(feat_std < 1e-8, 1.0, feat_std)
    X = (X - feat_mean) / feat_std

    # Time-ordered split
    split = max(1, int(n * (1.0 - val_fraction)))
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    # Build model
    model = _build_tcn(
        input_dim=N_FEATURES, hidden_dim=hidden_dim, num_blocks=num_blocks,
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
    criterion = nn.MSELoss()

    X_t = cpu_tensor(X_train, dtype=torch.float32)
    y_t = cpu_tensor(y_train, dtype=torch.float32)
    X_v = cpu_tensor(X_val, dtype=torch.float32)
    y_v = cpu_tensor(y_val, dtype=torch.float32)

    best_val_loss = float("inf")
    best_state = None
    patience = 0
    loss_history: list[dict] = []

    from app.services.bots.ml_job_progress import (
        cancelled_train_result,
        ml_cancel_requested,
        progress_path_from_config,
        write_ml_progress,
    )

    progress_path = progress_path_from_config(cfg)
    for epoch in range(epochs):
        if ml_cancel_requested(progress_path):
            return cancelled_train_result(symbol, "TCN_MULTI_HORIZON")
        model.train()
        indices = torch.randperm(len(X_t))
        epoch_loss = 0.0
        n_batches = 0

        for start in range(0, len(X_t), batch_size):
            idx = indices[start:start + batch_size]
            xb = X_t[idx].to(device, non_blocking=True)
            yb = y_t[idx].to(device, non_blocking=True)
            pred = model(xb)
            loss = criterion(pred, yb)
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            epoch_loss += loss.item()
            n_batches += 1

        avg_train_loss = epoch_loss / max(1, n_batches)
        model.eval()
        with torch.no_grad():
            val_loss = 0.0
            n_v = 0
            for vs in range(0, len(X_v), batch_size):
                xb = X_v[vs:vs + batch_size].to(device, non_blocking=True)
                yb = y_v[vs:vs + batch_size].to(device, non_blocking=True)
                val_loss += float(criterion(model(xb), yb).item()) * len(xb)
                n_v += len(xb)
            val_loss = val_loss / max(1, n_v)
        scheduler.step(val_loss)
        loss_history.append({
            "epoch": epoch + 1,
            "train_loss": round(avg_train_loss, 6),
            "val_loss": round(val_loss, 6),
        })
        write_ml_progress(
            progress_path,
            pct=min(20 + int(((epoch + 1) / max(epochs, 1)) * 70), 90),
            phase="epoch",
            detail=f"{epoch + 1}/{epochs}",
        )

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            patience = 0
        else:
            patience += 1
            if patience >= 10:
                break

    if best_state:
        model.load_state_dict(best_state)

    # Validation metrics
    model.eval()
    val_chunks: list = []
    with torch.no_grad():
        for vs in range(0, len(X_v), batch_size):
            xb = X_v[vs:vs + batch_size].to(device, non_blocking=True)
            val_chunks.append(model(xb).detach().cpu())
        val_pred = torch.cat(val_chunks, dim=0).numpy() if val_chunks else np.zeros((0, 3))

    # Directional accuracy per horizon
    train_device_meta = device_info(device)
    horizon_names = ["ret_5", "ret_15", "ret_60"]
    metrics: dict[str, Any] = {
        "train_samples": int(len(y_train)),
        "val_samples": int(len(y_val)),
        "val_mse": round(best_val_loss, 6),
        "epochs_trained": epoch + 1,
        "train_device": train_device_meta.get("device"),
    }
    for h, name in enumerate(horizon_names):
        correct = ((val_pred[:, h] > 0) == (y_val[:, h] > 0)).mean()
        metrics[f"dir_acc_{name}"] = round(float(correct), 4)

    # Export ONNX (single-file; Windows-safe across walk-forward re-exports)
    os.makedirs(_model_dir(symbol, tf), exist_ok=True)
    from app.services.bots.ml_model_artifacts import export_onnx_single_file

    export_onnx_single_file(
        model,
        torch.randn(1, lookback, N_FEATURES),
        _onnx_path(symbol, tf),
        input_names=["input"],
        output_names=["returns"],
        dynamic_axes={"input": {0: "batch"}, "returns": {0: "batch"}},
        opset_version=17,
        invalidate=lambda: _tcn_store.invalidate(symbol, timeframe=tf),
    )

    scaler = {"mean": feat_mean.tolist(), "std": feat_std.tolist()}
    with open(_scaler_path(symbol, tf), "w", encoding="utf-8") as fh:
        json.dump(scaler, fh, indent=2)

    metadata = {
        "symbol": symbol, "timeframe": tf, "model_type": "tcn_multi_horizon",
        "feature_schema_version": SIGNAL_FEATURE_VERSION,
        "horizons": horizon_names, "lookback": lookback,
        "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "metrics": metrics, "sample_count": n,
        "loss_history": loss_history,
        "config": {
            "lookback": lookback,
            "hidden_dim": hidden_dim,
            "num_blocks": num_blocks,
            "timeframe": tf,
            "train_device": train_device_meta,
        },
        "train_device": train_device_meta,
    }
    with open(_metadata_path(symbol, tf), "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)

    _tcn_store.invalidate(symbol, timeframe=tf)
    skip_snapshot = bool(cfg.get("skip_snapshot", cfg.get("_wf_mode", False)))
    if not skip_snapshot:
        try:
            from app.services.bots.ml_model_artifacts import snapshot_current_version
            snap = snapshot_current_version(_model_dir(symbol, tf), strategy="TCN_MULTI_HORIZON")
            if snap:
                metadata["version_id"] = snap.get("version_id")
                metadata["version_path"] = snap.get("path")
        except Exception:
            logger.exception("Failed to snapshot TCN version for %s", symbol)
    logger.info("TCN model trained for %s @ %s (n=%d, val_mse=%.6f)", symbol, tf, n, best_val_loss)
    return {"ok": True, "symbol": symbol, "timeframe": tf, **metadata}


# ── Model store ───────────────────────────────────────────────────────────


class TcnModelStore:
    def __init__(self) -> None:
        from app.config import ML_MODEL_CACHE_MAX, ML_MODEL_CACHE_TTL_SEC
        from app.services.bots.model_store_lru import bind_dict_cache

        self._sessions: dict[str, Any] = {}
        self._metadata: dict[str, dict] = {}
        self._scalers: dict[str, dict] = {}
        self._mtime: dict[str, float] = {}
        self._lru = bind_dict_cache(
            self._sessions, self._metadata, self._scalers, self._mtime,
            max_entries=ML_MODEL_CACHE_MAX,
            ttl_sec=ML_MODEL_CACHE_TTL_SEC,
        )

    @staticmethod
    def _cache_key(
        symbol: str,
        model_version: str | None,
        timeframe: str | None = None,
    ) -> str:
        from app.services.bots.ml_model_artifacts import model_storage_key

        return f"{model_storage_key(symbol, timeframe)}|{model_version or 'latest'}"

    def invalidate(self, symbol: str | None = None, *, timeframe: str | None = None):
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
            for d in (self._sessions, self._metadata, self._scalers, self._mtime):
                for k in list(d.keys()):
                    if any(k == p.rstrip("|") or k.startswith(p) for p in prefixes):
                        d.pop(k, None)
        else:
            self._lru.clear()
            self._sessions.clear()
            self._metadata.clear()
            self._scalers.clear()
            self._mtime.clear()

    def predict(
        self,
        symbol: str,
        window: np.ndarray,
        *,
        model_version: str | None = None,
        timeframe: str | None = None,
    ) -> np.ndarray | None:
        """Predict 3 forward returns from a (lookback, N_FEATURES) window."""
        key = self._cache_key(symbol, model_version, timeframe)
        session = self._ensure_loaded(
            symbol, model_version=model_version, timeframe=timeframe,
        )
        if session is None:
            return None
        scaler = self._scalers.get(key)
        if scaler:
            mean = np.array(scaler["mean"], dtype=np.float32)
            std = np.array(scaler["std"], dtype=np.float32)
            window = (window.astype(np.float32) - mean) / std
        try:
            returns = session.run(None, {"input": window.reshape(1, *window.shape).astype(np.float32)})[0][0]
            return returns
        except Exception as exc:
            logger.warning("TCN predict failed for %s: %s", symbol, exc)
            return None

    def _ensure_loaded(
        self,
        symbol: str,
        model_version: str | None = None,
        *,
        timeframe: str | None = None,
    ):
        from app.services.bots.ml_model_artifacts import resolve_model_dir

        key = self._cache_key(symbol, model_version, timeframe)
        load_dir = resolve_model_dir(_model_dir(symbol, timeframe), model_version)
        path = os.path.join(load_dir, "tcn_multi_horizon.onnx")
        if not os.path.isfile(path):
            return None
        mtime = os.path.getmtime(path)
        if key in self._sessions and self._mtime.get(key) == mtime:
            self._lru.touch(key)
            return self._sessions[key]
        try:
            import onnxruntime as ort
        except ImportError:
            return None
        try:
            meta_p = os.path.join(load_dir, "metadata.json")
            if os.path.isfile(meta_p):
                with open(meta_p, encoding="utf-8") as fh:
                    self._metadata[key] = json.load(fh)
            scaler_p = os.path.join(load_dir, "scaler.json")
            if os.path.isfile(scaler_p):
                with open(scaler_p, encoding="utf-8") as fh:
                    self._scalers[key] = json.load(fh)
            session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        except Exception as exc:
            logger.warning("TCN load failed for %s: %s", key, exc)
            return None
        self._sessions[key] = session
        self._mtime[key] = mtime
        self._lru.touch(key)
        return session


_tcn_store = TcnModelStore()

def get_tcn_store() -> TcnModelStore:
    return _tcn_store
