"""GNN Cross-Asset Signal Propagation — training pipeline.

Models the trading universe as a graph where symbols are nodes and edges
represent correlation strength.  A simple Graph Attention Network (GAT)
propagates momentum signals across correlated assets.

Designed to capture lead-lag relationships: if BTC breaks out and ETH
hasn't moved yet, the GNN learns that ETH is likely to follow.

Architecture: 2-layer GAT (~30K params) operating on per-symbol feature
vectors with correlation-weighted adjacency.
"""

from __future__ import annotations

import json
import logging
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

GNN_MODEL_DIR = os.path.join(BASE_DIR, "data", "gnn_signal_models")
N_FEATURES = len(SIGNAL_FEATURE_NAMES)


def _model_dir(basket: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(basket).upper())
    return os.path.join(GNN_MODEL_DIR, safe)

def _onnx_path(basket: str) -> str:
    return os.path.join(_model_dir(basket), "gnn_cross_asset.onnx")

def _metadata_path(basket: str) -> str:
    return os.path.join(_model_dir(basket), "metadata.json")

def _scaler_path(basket: str) -> str:
    return os.path.join(_model_dir(basket), "scaler.json")


def _get_torch():
    try:
        import torch
        import torch.nn as nn
        return torch, nn
    except ImportError as exc:
        raise RuntimeError("PyTorch required for GNN training") from exc


# ── Graph Attention Layer ─────────────────────────────────────────────────


def _build_gat(input_dim: int = N_FEATURES, hidden_dim: int = 64,
               output_dim: int = 3, n_heads: int = 4):
    """Build a 2-layer Graph Attention Network.

    Operates on dense adjacency (small graphs of 5-20 assets).
    No PyG dependency — pure PyTorch implementation.
    """
    torch, nn = _get_torch()

    class GraphAttentionLayer(nn.Module):
        def __init__(self, in_features, out_features, n_heads=4, dropout=0.2):
            super().__init__()
            self.n_heads = n_heads
            self.head_dim = out_features // n_heads
            self.W = nn.Linear(in_features, out_features, bias=False)
            self.attn_src = nn.Parameter(torch.randn(n_heads, self.head_dim))
            self.attn_dst = nn.Parameter(torch.randn(n_heads, self.head_dim))
            self.leaky_relu = nn.LeakyReLU(0.2)
            self.dropout = nn.Dropout(dropout)

        def forward(self, x, adj):
            # x: (N, in_features), adj: (N, N)
            N = x.size(0)
            h = self.W(x).view(N, self.n_heads, self.head_dim)

            # Attention scores
            attn_src = (h * self.attn_src).sum(-1)  # (N, n_heads)
            attn_dst = (h * self.attn_dst).sum(-1)  # (N, n_heads)
            attn = attn_src.unsqueeze(1) + attn_dst.unsqueeze(0)  # (N, N, n_heads)
            attn = self.leaky_relu(attn)

            # Mask with adjacency
            mask = (adj.unsqueeze(-1) > 0).float()
            attn = attn * mask + (1 - mask) * (-1e9)
            attn = torch.softmax(attn, dim=1)
            attn = self.dropout(attn)

            # Aggregate
            out = torch.einsum("ijh,jhd->ihd", attn, h)
            return out.reshape(N, -1)

    class GATNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.gat1 = GraphAttentionLayer(input_dim, hidden_dim, n_heads)
            self.gat2 = GraphAttentionLayer(hidden_dim, hidden_dim, n_heads)
            self.relu = nn.ReLU()
            self.classifier = nn.Linear(hidden_dim, output_dim)

        def forward(self, x, adj):
            # x: (N, input_dim), adj: (N, N)
            h = self.relu(self.gat1(x, adj))
            h = self.relu(self.gat2(h, adj))
            return self.classifier(h)  # (N, output_dim) — per-node logits

    return GATNet()


# ── Correlation-based adjacency ──────────────────────────────────────────


def build_adjacency_from_correlations(
    symbol_returns: dict[str, list[float]],
    min_corr: float = 0.3,
) -> tuple[list[str], np.ndarray]:
    """Build adjacency matrix from pairwise return correlations.

    Parameters
    ----------
    symbol_returns : dict mapping symbol → list of returns
    min_corr : float, minimum absolute correlation to create an edge

    Returns
    -------
    symbols : ordered list of symbol names
    adj : np.ndarray (N, N) adjacency with correlation strengths
    """
    symbols = sorted(symbol_returns.keys())
    n = len(symbols)
    adj = np.eye(n, dtype=np.float32)  # self-loops

    for i in range(n):
        for j in range(i + 1, n):
            ri = np.array(symbol_returns[symbols[i]])
            rj = np.array(symbol_returns[symbols[j]])
            min_len = min(len(ri), len(rj))
            if min_len < 20:
                continue
            ri, rj = ri[:min_len], rj[:min_len]
            corr = float(np.corrcoef(ri, rj)[0, 1])
            if abs(corr) >= min_corr:
                adj[i, j] = abs(corr)
                adj[j, i] = abs(corr)

    return symbols, adj


# ── Model store ───────────────────────────────────────────────────────────


class GnnModelStore:
    """Stores GNN models per basket (not per symbol — GNN operates on baskets)."""

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
    def _cache_key(basket: str, model_version: str | None) -> str:
        return f"{str(basket).upper()}|{model_version or 'latest'}"

    def invalidate(self, basket=None):
        if basket:
            prefix = str(basket).upper() + "|"
            self._lru.discard_prefix(str(basket).upper())
            self._lru.discard_prefix(prefix)
            for d in (self._sessions, self._metadata, self._scalers, self._mtime):
                for k in list(d.keys()):
                    if k == str(basket).upper() or k.startswith(prefix):
                        d.pop(k, None)
        else:
            self._lru.clear()
            for d in (self._sessions, self._metadata, self._scalers, self._mtime):
                d.clear()

    def predict(
        self,
        basket: str,
        node_features: np.ndarray,
        adj: np.ndarray,
        *,
        model_version: str | None = None,
    ) -> np.ndarray | None:
        """Predict per-node signal logits.

        Parameters
        ----------
        basket : str identifier
        node_features : (N, N_FEATURES) per-symbol features
        adj : (N, N) adjacency matrix

        Returns (N, 3) logits or None.
        """
        key = self._cache_key(basket, model_version)
        session = self._ensure_loaded(basket, model_version=model_version)
        if session is None:
            return None

        scaler = self._scalers.get(key)
        if scaler:
            m = np.array(scaler["mean"], dtype=np.float32)
            s = np.array(scaler["std"], dtype=np.float32)
            node_features = (node_features.astype(np.float32) - m) / s

        try:
            logits = session.run(None, {
                "node_features": node_features.astype(np.float32),
                "adjacency": adj.astype(np.float32),
            })[0]
            return logits
        except Exception as exc:
            logger.warning("GNN predict failed for %s: %s", basket, exc)
            return None

    def _ensure_loaded(self, basket: str, model_version: str | None = None):
        from app.services.bots.ml_model_artifacts import resolve_model_dir

        key = self._cache_key(basket, model_version)
        load_dir = resolve_model_dir(_model_dir(basket), model_version)
        path = os.path.join(load_dir, "gnn_cross_asset.onnx")
        if not os.path.isfile(path):
            return None
        mt = os.path.getmtime(path)
        if key in self._sessions and self._mtime.get(key) == mt:
            self._lru.touch(key)
            return self._sessions[key]
        try:
            import onnxruntime as ort
        except ImportError:
            return None
        try:
            mp = os.path.join(load_dir, "metadata.json")
            if os.path.isfile(mp):
                with open(mp, encoding="utf-8") as f:
                    self._metadata[key] = json.load(f)
            sp = os.path.join(load_dir, "scaler.json")
            if os.path.isfile(sp):
                with open(sp, encoding="utf-8") as f:
                    self._scalers[key] = json.load(f)
            s = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        except Exception:
            return None
        self._sessions[key] = s
        self._mtime[key] = mt
        self._lru.touch(key)
        return s


_gnn_store = GnnModelStore()

def get_gnn_store():
    return _gnn_store


# ── Training ───────────────────────────────────────────────────────────────


def train_gnn_model(
    symbol: str,
    candles: list[dict],
    config: dict | None = None,
    *,
    epochs: int = 40,
) -> dict[str, Any]:
    """Train a GAT on single-node graphs from one symbol's bars.

    Multi-symbol baskets can be trained later by passing ``basket_id`` and a
    pre-built feature matrix; this API path stores artifacts under
    ``basket_id or symbol`` so status / pin / versioning work like other ML models.
    """
    from app.services.bots.ml_triple_barrier import label_distribution, label_triple_barrier

    torch, nn = _get_torch()
    cfg = merge_strategy_config("GNN_CROSS_ASSET", config or {})
    epochs = int(cfg.get("epochs", epochs))
    basket = str(cfg.get("basket_id") or symbol or "").upper()
    if not basket:
        return {"ok": False, "error": "basket_id or symbol required"}

    hidden = int(cfg.get("hidden_dim", 64))
    n_heads = int(cfg.get("n_heads", 4))
    lr = float(cfg.get("learning_rate", 0.001))
    batch_size = int(cfg.get("batch_size", 64))
    min_samples = int(cfg.get("min_train_samples", 200))
    val_frac = float(cfg.get("val_fraction", 0.2))
    atr_mult = float(cfg.get("triple_barrier_atr_mult", 2.0))
    max_bars = int(cfg.get("triple_barrier_max_bars", 30))
    min_corr = float(cfg.get("min_corr", 0.3))

    if len(candles) < 250:
        return {"ok": False, "error": "insufficient candles", "symbol": symbol, "basket_id": basket}

    labels = label_triple_barrier(
        candles, atr_mult_upper=atr_mult, atr_mult_lower=atr_mult, max_holding_bars=max_bars
    )
    dist = label_distribution(labels)
    label_map = {1: 0, 0: 1, -1: 2}  # BUY / NONE / SELL
    reverse_map = {0: "BUY", 1: "NONE", 2: "SELL"}

    X_list: list[np.ndarray] = []
    y_list: list[int] = []
    for idx, label_info in enumerate(labels):
        if label_info.get("barrier_hit") == "invalid":
            continue
        if idx < 20:
            continue
        if idx >= len(candles) - max_bars:
            continue
        lookback = [dict(c) for c in candles[max(0, idx - 20):idx]]
        feats = bar_to_signal_features(candles[idx], lookback_rows=lookback)
        X_list.append(signal_features_to_vector(feats).astype(np.float32))
        y_list.append(label_map[int(label_info["label"])])

    n = len(y_list)
    if n < min_samples:
        return {
            "ok": False,
            "error": f"insufficient labelled samples ({n} < {min_samples})",
            "symbol": symbol,
            "basket_id": basket,
        }

    X = np.stack(X_list, axis=0)
    y = np.asarray(y_list, dtype=np.int64)
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std = np.where(std < 1e-8, 1.0, std)
    X = (X - mean) / std

    split = max(1, int(n * (1 - val_frac)))
    X_tr, X_va = X[:split], X[split:]
    y_tr, y_va = y[:split], y[split:]

    model = _build_gat(N_FEATURES, hidden, 3, n_heads)
    class_counts = np.bincount(y_tr, minlength=3).astype(np.float32)
    class_counts = np.maximum(class_counts, 1.0)
    weights = torch.tensor((1.0 / class_counts) * class_counts.sum() / 3.0)
    criterion = nn.CrossEntropyLoss(weight=weights)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)

    adj_one = torch.ones(1, 1, dtype=torch.float32)
    X_t = torch.tensor(X_tr)
    y_t = torch.tensor(y_tr)
    X_v = torch.tensor(X_va)
    y_v = torch.tensor(y_va)

    best_val, best_state, pat = float("inf"), None, 0
    loss_history: list[dict] = []
    from app.services.bots.ml_job_progress import (
        cancelled_train_result,
        ml_cancel_requested,
        progress_path_from_config,
    )

    progress_path = progress_path_from_config(cfg)
    for ep in range(epochs):
        if ml_cancel_requested(progress_path):
            return cancelled_train_result(basket, "GNN_CROSS_ASSET")
        model.train()
        idx = torch.randperm(len(X_t))
        ep_loss = 0.0
        n_batches = 0
        for s in range(0, len(X_t), batch_size):
            b = idx[s:s + batch_size]
            optimizer.zero_grad()
            # Batched single-node graphs: run per sample (small N=1)
            logits = []
            for j in b:
                logits.append(model(X_t[j].unsqueeze(0), adj_one)[0])
            logits_t = torch.stack(logits, dim=0)
            loss = criterion(logits_t, y_t[b])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            ep_loss += float(loss.item())
            n_batches += 1

        avg_train = ep_loss / max(1, n_batches)
        model.eval()
        with torch.no_grad():
            va_logits = []
            for j in range(len(X_v)):
                va_logits.append(model(X_v[j].unsqueeze(0), adj_one)[0])
            va_stack = torch.stack(va_logits, dim=0)
            vl = float(criterion(va_stack, y_v).item())
            va_pred = va_stack.argmax(1).numpy()
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
        va_logits = []
        for j in range(len(X_v)):
            va_logits.append(model(X_v[j].unsqueeze(0), adj_one)[0])
        va_pred = torch.stack(va_logits, dim=0).argmax(1).numpy()
    acc = float((va_pred == y_va).mean()) if len(y_va) else 0.0

    os.makedirs(_model_dir(basket), exist_ok=True)
    from app.services.bots.ml_model_artifacts import export_onnx_single_file

    export_onnx_single_file(
        model,
        (torch.randn(1, N_FEATURES), torch.ones(1, 1)),
        _onnx_path(basket),
        input_names=["node_features", "adjacency"],
        output_names=["logits"],
        dynamic_axes={
            "node_features": {0: "n"},
            "adjacency": {0: "n", 1: "n"},
            "logits": {0: "n"},
        },
        opset_version=17,
        invalidate=lambda: get_gnn_store().invalidate(basket),
    )

    with open(_scaler_path(basket), "w", encoding="utf-8") as f:
        json.dump({"mean": mean.tolist(), "std": std.tolist()}, f, indent=2)

    meta = {
        "symbol": str(symbol).upper(),
        "basket_id": basket,
        "model_type": "gnn_cross_asset",
        "feature_schema_version": SIGNAL_FEATURE_VERSION,
        "feature_names": list(SIGNAL_FEATURE_NAMES),
        "reverse_map": {str(k): v for k, v in reverse_map.items()},
        "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sample_count": n,
        "label_distribution": dist,
        "metrics": {
            "val_accuracy": round(acc, 4),
            "val_loss": round(best_val, 4),
            "train_samples": int(len(y_tr)),
            "val_samples": int(len(y_va)),
        },
        "config": {
            "hidden_dim": hidden,
            "n_heads": n_heads,
            "min_corr": min_corr,
            "basket_id": basket,
        },
        "loss_history": loss_history,
    }
    with open(_metadata_path(basket), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    _gnn_store.invalidate(basket)
    skip_snapshot = bool(cfg.get("skip_snapshot", cfg.get("_wf_mode", False)))
    if not skip_snapshot:
        try:
            from app.services.bots.ml_model_artifacts import snapshot_current_version
            snap = snapshot_current_version(_model_dir(basket), strategy="GNN_CROSS_ASSET")
            if snap:
                meta["version_id"] = snap.get("version_id")
                meta["version_path"] = snap.get("path")
        except Exception:
            logger.exception("Failed to snapshot GNN version for %s", basket)

    logger.info("GNN model trained for basket %s (n=%d, val_acc=%.3f)", basket, n, acc)
    return {"ok": True, "symbol": str(symbol).upper(), "basket_id": basket, **meta}
