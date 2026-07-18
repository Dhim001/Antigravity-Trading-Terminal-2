"""Transformer Attention-Based Signal Generator — training pipeline.

Lightweight Transformer encoder (4 layers, ~60K params) that processes
bar sequences and learns which historical bars are most relevant via
self-attention.  Outputs 3-class prediction (BUY/SELL/NONE).

Key advantage: interpretable attention weights show *which past bars*
influenced the decision.
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

TRANSFORMER_MODEL_DIR = os.path.join(BASE_DIR, "data", "transformer_signal_models")
N_FEATURES = len(SIGNAL_FEATURE_NAMES)
N_CLASSES = 3
LABEL_MAP = {1: 0, 0: 1, -1: 2}
REVERSE_MAP = {0: "BUY", 1: "NONE", 2: "SELL"}


def _model_dir(symbol: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(symbol).upper())
    return os.path.join(TRANSFORMER_MODEL_DIR, safe)

def _onnx_path(symbol: str) -> str:
    return os.path.join(_model_dir(symbol), "transformer_signal.onnx")

def _metadata_path(symbol: str) -> str:
    return os.path.join(_model_dir(symbol), "metadata.json")

def _scaler_path(symbol: str) -> str:
    return os.path.join(_model_dir(symbol), "scaler.json")


def _get_torch():
    try:
        import torch
        import torch.nn as nn
        return torch, nn
    except ImportError as exc:
        raise RuntimeError("PyTorch required for Transformer training") from exc


# ── Model ─────────────────────────────────────────────────────────────────


def _build_transformer(input_dim: int = N_FEATURES, d_model: int = 64,
                       nhead: int = 4, num_layers: int = 4,
                       seq_len: int = 60, num_classes: int = N_CLASSES):
    """Build a lightweight Transformer encoder for signal classification."""
    torch, nn = _get_torch()

    class PositionalEncoding(nn.Module):
        def __init__(self, d_model, max_len=200):
            super().__init__()
            pe = torch.zeros(max_len, d_model)
            position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
            div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
            pe[:, 0::2] = torch.sin(position * div_term)
            pe[:, 1::2] = torch.cos(position * div_term[:d_model // 2])
            self.register_buffer("pe", pe.unsqueeze(0))

        def forward(self, x):
            return x + self.pe[:, :x.size(1)]

    class TransformerSignalNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.input_proj = nn.Linear(input_dim, d_model)
            self.pos_enc = PositionalEncoding(d_model, max_len=seq_len + 10)
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=nhead, dim_feedforward=d_model * 2,
                dropout=0.1, batch_first=True,
            )
            self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
            self.classifier = nn.Linear(d_model, num_classes)

        def forward(self, x):
            # x: (batch, seq_len, input_dim)
            x = self.input_proj(x)
            x = self.pos_enc(x)
            x = self.encoder(x)
            # Use [CLS]-like approach: mean pooling over sequence
            x = x.mean(dim=1)
            return self.classifier(x)

    return TransformerSignalNet()


# ── Sequence building (same as LSTM) ──────────────────────────────────────


def build_transformer_sequences(
    candles: list[dict], labels: list[dict], *, lookback: int = 60, max_holding_bars: int = 30,
) -> tuple[np.ndarray, np.ndarray]:
    n = len(candles)
    feature_lb = 20
    seqs_x, seqs_y = [], []

    for i in range(lookback + feature_lb, n - max_holding_bars):
        lbl = labels[i]
        if lbl.get("barrier_hit") == "invalid":
            continue
        window = []
        for j in range(i - lookback + 1, i + 1):
            lb_start = max(0, j - feature_lb)
            feat = bar_to_signal_features(candles[j], lookback_rows=candles[lb_start:j])
            window.append(signal_features_to_vector(feat))
        seqs_x.append(np.stack(window))
        seqs_y.append(LABEL_MAP[lbl["label"]])

    if not seqs_x:
        return np.array([]), np.array([])
    return np.stack(seqs_x).astype(np.float32), np.array(seqs_y, dtype=np.int64)


# ── Training ──────────────────────────────────────────────────────────────


def train_transformer_model(
    symbol: str, candles: list[dict], *, config: dict | None = None, epochs: int = 50,
) -> dict[str, Any]:
    torch, nn = _get_torch()
    cfg = merge_strategy_config("TRANSFORMER_SIGNAL", config or {})
    lookback = int(cfg.get("lookback", 60))
    d_model = int(cfg.get("d_model", 64))
    nhead = int(cfg.get("nhead", 4))
    n_layers = int(cfg.get("num_layers", 4))
    lr = float(cfg.get("learning_rate", 0.0005))
    batch_size = int(cfg.get("batch_size", 64))
    min_samples = int(cfg.get("min_train_samples", 300))
    val_frac = float(cfg.get("val_fraction", 0.2))
    atr_mult = float(cfg.get("triple_barrier_atr_mult", 2.0))
    max_bars = int(cfg.get("triple_barrier_max_bars", 30))

    if len(candles) < lookback + 120:
        return {"ok": False, "error": "insufficient candles", "symbol": symbol}

    labels = label_triple_barrier(candles, atr_mult_upper=atr_mult, atr_mult_lower=atr_mult, max_holding_bars=max_bars)
    dist = label_distribution(labels)
    X, y = build_transformer_sequences(candles, labels, lookback=lookback, max_holding_bars=max_bars)
    n = len(y)
    if n < min_samples:
        return {"ok": False, "error": f"insufficient sequences ({n})", "symbol": symbol}

    # Normalize
    flat = X.reshape(-1, N_FEATURES)
    mean, std = flat.mean(0), flat.std(0)
    std = np.where(std < 1e-8, 1.0, std)
    X = (X - mean) / std

    split = max(1, int(n * (1 - val_frac)))
    X_tr, X_va = X[:split], X[split:]
    y_tr, y_va = y[:split], y[split:]

    model = _build_transformer(N_FEATURES, d_model, nhead, n_layers, lookback)
    class_counts = np.bincount(y_tr, minlength=N_CLASSES).astype(np.float32)
    class_counts = np.maximum(class_counts, 1.0)
    weights = torch.tensor((1.0 / class_counts) * class_counts.sum() / N_CLASSES)
    criterion = nn.CrossEntropyLoss(weight=weights)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)

    X_t, y_t = torch.tensor(X_tr), torch.tensor(y_tr)
    X_v, y_v = torch.tensor(X_va), torch.tensor(y_va)

    best_val, best_state, pat = float("inf"), None, 0
    loss_history: list[dict] = []
    for ep in range(epochs):
        model.train()
        idx = torch.randperm(len(X_t))
        ep_loss = 0.0
        n_batches = 0
        for s in range(0, len(X_t), batch_size):
            b = idx[s:s + batch_size]
            optimizer.zero_grad()
            loss = criterion(model(X_t[b]), y_t[b])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            ep_loss += loss.item()
            n_batches += 1

        avg_train = ep_loss / max(1, n_batches)
        model.eval()
        with torch.no_grad():
            vl = criterion(model(X_v), y_v).item()
        loss_history.append({
            "epoch": ep + 1,
            "train_loss": round(avg_train, 6),
            "val_loss": round(vl, 6),
        })
        if vl < best_val:
            best_val = vl
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            pat = 0
        else:
            pat += 1
            if pat >= 10:
                break

    if best_state:
        model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        va_pred = model(X_v).argmax(1).numpy()
    acc = float((va_pred == y_va).mean())

    # ONNX export (single-file; Windows-safe across walk-forward re-exports)
    os.makedirs(_model_dir(symbol), exist_ok=True)
    from app.services.bots.ml_model_artifacts import export_onnx_single_file

    export_onnx_single_file(
        model,
        torch.randn(1, lookback, N_FEATURES),
        _onnx_path(symbol),
        input_names=["input"],
        output_names=["logits"],
        dynamic_axes={"input": {0: "b"}, "logits": {0: "b"}},
        opset_version=17,
        invalidate=lambda: _transformer_store.invalidate(symbol),
    )

    with open(_scaler_path(symbol), "w") as f:
        json.dump({"mean": mean.tolist(), "std": std.tolist()}, f, indent=2)

    meta = {
        "symbol": symbol, "model_type": "transformer_signal",
        "feature_schema_version": SIGNAL_FEATURE_VERSION,
        "reverse_map": {str(k): v for k, v in REVERSE_MAP.items()},
        "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "metrics": {"val_accuracy": round(acc, 4), "val_loss": round(best_val, 4),
                     "train_samples": int(len(y_tr)), "val_samples": int(len(y_va))},
        "config": {"lookback": lookback, "d_model": d_model, "nhead": nhead, "num_layers": n_layers},
        "loss_history": loss_history,
    }
    with open(_metadata_path(symbol), "w") as f:
        json.dump(meta, f, indent=2)

    _transformer_store.invalidate(symbol)
    try:
        from app.services.bots.ml_model_artifacts import snapshot_current_version
        snap = snapshot_current_version(_model_dir(symbol), strategy="TRANSFORMER_SIGNAL")
        if snap:
            meta["version_id"] = snap.get("version_id")
            meta["version_path"] = snap.get("path")
    except Exception:
        logger.exception("Failed to snapshot Transformer version for %s", symbol)
    return {"ok": True, "symbol": symbol, **meta}


# ── Model store ───────────────────────────────────────────────────────────


class TransformerModelStore:
    def __init__(self):
        self._sessions, self._metadata, self._scalers, self._mtime = {}, {}, {}, {}

    @staticmethod
    def _cache_key(symbol, model_version):
        return f"{str(symbol).upper()}|{model_version or 'latest'}"

    def invalidate(self, symbol=None):
        if symbol:
            prefix = str(symbol).upper() + "|"
            for d in (self._sessions, self._metadata, self._scalers, self._mtime):
                for k in list(d.keys()):
                    if k == str(symbol).upper() or k.startswith(prefix):
                        d.pop(k, None)
        else:
            for d in (self._sessions, self._metadata, self._scalers, self._mtime):
                d.clear()

    def predict(self, symbol, window, *, model_version=None):
        key = self._cache_key(symbol, model_version)
        session = self._ensure_loaded(symbol, model_version=model_version)
        if session is None:
            return None
        scaler = self._scalers.get(key)
        if scaler:
            m, s = np.array(scaler["mean"], dtype=np.float32), np.array(scaler["std"], dtype=np.float32)
            window = (window.astype(np.float32) - m) / s
        try:
            logits = session.run(None, {"input": window.reshape(1, *window.shape).astype(np.float32)})[0][0]
            x = logits - logits.max()
            proba = np.exp(x) / np.exp(x).sum()
            idx = int(np.argmax(proba))
            meta = self._metadata.get(key, {})
            rmap = meta.get("reverse_map", REVERSE_MAP)
            return rmap.get(str(idx), "NONE"), float(proba[idx])
        except Exception as e:
            logger.warning("Transformer predict failed for %s: %s", symbol, e)
            return None

    def _ensure_loaded(self, symbol, model_version=None):
        from app.services.bots.ml_model_artifacts import resolve_model_dir

        key = self._cache_key(symbol, model_version)
        load_dir = resolve_model_dir(_model_dir(symbol), model_version)
        path = os.path.join(load_dir, "transformer_signal.onnx")
        if not os.path.isfile(path):
            return None
        mt = os.path.getmtime(path)
        if key in self._sessions and self._mtime.get(key) == mt:
            return self._sessions[key]
        try:
            import onnxruntime as ort
        except ImportError:
            return None
        try:
            mp = os.path.join(load_dir, "metadata.json")
            if os.path.isfile(mp):
                with open(mp) as f:
                    self._metadata[key] = json.load(f)
            sp = os.path.join(load_dir, "scaler.json")
            if os.path.isfile(sp):
                with open(sp) as f:
                    self._scalers[key] = json.load(f)
            s = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        except Exception:
            return None
        self._sessions[key] = s
        self._mtime[key] = mt
        return s


_transformer_store = TransformerModelStore()

def get_transformer_store():
    return _transformer_store
