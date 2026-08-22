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
    EVAL_FEATURE_LOOKBACK,
    SIGNAL_FEATURE_NAMES,
    SIGNAL_FEATURE_VERSION,
    precompute_signal_feature_matrix,
)
from app.services.bots.ml_triple_barrier import label_distribution, label_triple_barrier

logger = logging.getLogger(__name__)

TRANSFORMER_MODEL_DIR = os.path.join(BASE_DIR, "data", "transformer_signal_models")
N_FEATURES = len(SIGNAL_FEATURE_NAMES)
N_CLASSES = 3
LABEL_MAP = {1: 0, 0: 1, -1: 2}
REVERSE_MAP = {0: "BUY", 1: "NONE", 2: "SELL"}


def _model_dir(symbol: str, timeframe: str | None = None) -> str:
    from app.services.bots.ml_model_artifacts import model_storage_key

    return os.path.join(TRANSFORMER_MODEL_DIR, model_storage_key(symbol, timeframe))

def _onnx_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "transformer_signal.onnx")

def _metadata_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "metadata.json")

def _scaler_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "scaler.json")


def _checkpoint_path(symbol: str, timeframe: str | None = None) -> str:
    """Trainable state_dict sidecar (cross-asset transfer donor)."""
    return os.path.join(_model_dir(symbol, timeframe), "checkpoint.pt")


def _get_torch():
    try:
        import torch
        import torch.nn as nn
        return torch, nn
    except ImportError as exc:
        raise RuntimeError("PyTorch required for Transformer training") from exc


# ── Model ─────────────────────────────────────────────────────────────────


def _build_transformer(input_dim: int = N_FEATURES, d_model: int = 128,
                       nhead: int = 4, num_layers: int = 6,
                       seq_len: int = 90, num_classes: int = N_CLASSES):
    """Build a lightweight Transformer encoder for signal classification."""
    torch, nn = _get_torch()

    d_model = max(8, int(d_model))
    nhead = max(1, int(nhead))
    # MultiheadAttention requires embed_dim % num_heads == 0.
    if d_model % nhead != 0:
        snapped = max(nhead, (d_model // nhead) * nhead)
        logger.warning(
            "Transformer d_model=%d not divisible by nhead=%d — using d_model=%d",
            d_model, nhead, snapped,
        )
        d_model = snapped

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
    candles: list[dict],
    labels: list[dict],
    *,
    lookback: int = 90,
    max_holding_bars: int = 30,
    feat_matrix: np.ndarray | None = None,
    config: dict | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    from app.services.bots.ml_event_sampling import keep_train_row, sample_weight_for_label
    from app.services.bots.ml_feature_engineering import apply_exclude_features, resolve_exclude_features

    n = len(candles)
    feature_lb = EVAL_FEATURE_LOOKBACK
    feature_warmup = 20
    if feat_matrix is None or len(feat_matrix) != n:
        feat_matrix = precompute_signal_feature_matrix(
            candles, feature_lookback=feature_lb,
        )
    feat_matrix = apply_exclude_features(feat_matrix, resolve_exclude_features(config))
    seqs_x, seqs_y, seqs_w = [], [], []

    for i in range(lookback + feature_warmup, n - max_holding_bars):
        if i >= len(labels):
            break
        lbl = labels[i]
        if not keep_train_row(lbl, config):
            continue
        mapped = LABEL_MAP.get(lbl.get("label"))
        if mapped is None:
            continue
        seqs_x.append(feat_matrix[i - lookback + 1 : i + 1])
        seqs_y.append(mapped)
        seqs_w.append(sample_weight_for_label(lbl))

    if not seqs_x:
        return np.array([]), np.array([]), np.array([])
    return (
        np.stack(seqs_x).astype(np.float32),
        np.array(seqs_y, dtype=np.int64),
        np.array(seqs_w, dtype=np.float32),
    )


# ── Training ──────────────────────────────────────────────────────────────


def train_transformer_model(
    symbol: str, candles: list[dict], *, config: dict | None = None, epochs: int = 80,
) -> dict[str, Any]:
    torch, nn = _get_torch()
    raw_cfg = dict(config or {})
    cfg = merge_strategy_config("TRANSFORMER_SIGNAL", raw_cfg)
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe
    from app.services.bots.ml_training_window import (
        allow_weight_warm_start,
        apply_champion_train_overrides,
    )

    tf = normalize_model_timeframe(cfg.get("timeframe") or raw_cfg.get("timeframe"))
    cfg["timeframe"] = tf
    cfg = apply_champion_train_overrides(cfg, raw_cfg)
    epochs = int(cfg.get("epochs", epochs))
    lookback = int(cfg.get("lookback", 90))
    d_model = int(cfg.get("d_model", 128))
    nhead = int(cfg.get("nhead", 4))
    n_layers = int(cfg.get("num_layers", 4))
    lr = float(cfg.get("learning_rate", 0.0005))
    if bool(cfg.get("_wf_mode")) and not cfg.get("champion_train") and "min_train_samples" not in raw_cfg:
        min_samples = int(cfg.get("wf_min_train_samples", 150))
    else:
        min_samples = int(cfg.get("min_train_samples", 300))
    # Interactive WF: smaller net so folds finish; capacity parity keeps
    # full Lab architecture for accurate OOS.
    if (
        bool(cfg.get("_wf_mode"))
        and not cfg.get("champion_train")
        and not bool(cfg.get("wf_capacity_parity", True))
    ):
        lookback = min(lookback, int(cfg.get("wf_lookback", 60)))
        d_model = min(d_model, int(cfg.get("wf_d_model", 64)))
        n_layers = min(n_layers, int(cfg.get("wf_num_layers", 2)))
        nhead = min(nhead, 4)
        if d_model % nhead != 0:
            nhead = max(1, d_model // 32) or 1
            while d_model % nhead != 0 and nhead > 1:
                nhead -= 1
    val_frac = float(cfg.get("val_fraction", 0.2))
    atr_mult = float(cfg.get("triple_barrier_atr_mult", 2.0))
    max_bars = int(cfg.get("triple_barrier_max_bars", 30))
    from app.services.bots.ml_feature_cache import (
        resolve_precomputed_features,
        resolve_precomputed_labels,
    )
    from app.services.bots.ml_torch_device import (
        cap_wf_epochs,
        cpu_tensor,
        device_info,
        ensure_cuda_ready,
        make_torch_dataloader,
        resolve_torch_device,
        resolve_wf_torch_device,
        suggest_batch_size,
        unpack_batch_to_device,
    )

    if bool(cfg.get("_wf_mode")) and not cfg.get("champion_train"):
        epochs = cap_wf_epochs(epochs, cfg, default=8)
        device = resolve_wf_torch_device(cfg)
    else:
        device = resolve_torch_device(cfg)
    batch_size = suggest_batch_size(cfg, 64, device=device)
    ensure_cuda_ready(device)
    from app.services.bots.ml_job_progress import (
        cancelled_train_result,
        ml_cancel_requested,
        progress_path_from_config,
        write_ml_progress,
    )

    progress_path = progress_path_from_config(cfg)
    write_ml_progress(
        progress_path, pct=8, phase="device",
        detail=f"{device}" + (
            " · CPU is slow for Transformer WF — install CUDA torch in .venv"
            if getattr(device, "type", None) == "cpu" else ""
        ),
    )

    if len(candles) < lookback + 120:
        return {"ok": False, "error": "insufficient candles", "symbol": symbol}

    labels = resolve_precomputed_labels(candles, cfg)
    if labels is None:
        labels = label_triple_barrier(
            candles, atr_mult_upper=atr_mult, atr_mult_lower=atr_mult, max_holding_bars=max_bars,
        )
    from app.services.bots.ml_event_sampling import annotate_event_labels, class_adjusted_weights, event_filter_metadata
    from app.services.bots.ml_feature_engineering import apply_exclude_features, feature_scheme_metadata, resolve_exclude_features

    labels = annotate_event_labels(labels, candles, cfg)
    dist = label_distribution(labels)
    pre_feat = resolve_precomputed_features(candles, cfg)
    if pre_feat is not None:
        pre_feat = apply_exclude_features(pre_feat, resolve_exclude_features(cfg))
    X, y, w = build_transformer_sequences(
        candles, labels, lookback=lookback, max_holding_bars=max_bars,
        feat_matrix=pre_feat, config=cfg,
    )
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
    w_tr, w_va = w[:split], w[split:]

    model = _build_transformer(N_FEATURES, d_model, nhead, n_layers, lookback).to(device)

    # Cross-asset donor warm-start (never for WF folds — honest OOS).
    donor_lineage: dict[str, Any] | None = None
    if not bool(cfg.get("_wf_mode")) and allow_weight_warm_start(cfg):
        from app.services.bots import model_transfer as _mt

        _ws = _mt.load_donor_weights(
            model, "TRANSFORMER_SIGNAL", cfg.get("donor"), tf,
            device=device, head_prefixes=("classifier",),
        )
        if _ws:
            from app.config import ML_WARM_START_EPOCHS, ML_WARM_START_LR_FACTOR

            donor_lineage = _ws["lineage"]
            epochs = max(1, int(ML_WARM_START_EPOCHS))
            lr = lr * float(ML_WARM_START_LR_FACTOR)
            donor_lineage["finetune_budget"] = {
                **(donor_lineage.get("finetune_budget") or {}),
                "epochs": epochs,
                "learning_rate": lr,
            }

    class_counts = np.bincount(y_tr, minlength=N_CLASSES).astype(np.float32)
    class_counts = np.maximum(class_counts, 1.0)
    w_arr = (1.0 / class_counts) * class_counts.sum() / N_CLASSES
    w_arr = np.clip(w_arr, 0.5, 5.0)
    criterion = nn.CrossEntropyLoss(reduction="none", label_smoothing=0.1)
    optimizer = torch.optim.AdamW(
        (p for p in model.parameters() if p.requires_grad), lr=lr, weight_decay=1e-4,
    )

    X_t = cpu_tensor(X_tr, dtype=torch.float32)
    y_t = cpu_tensor(y_tr, dtype=torch.long)
    w_t = cpu_tensor(class_adjusted_weights(w_tr, y_tr, w_arr), dtype=torch.float32)
    X_v = cpu_tensor(X_va, dtype=torch.float32)
    y_v = cpu_tensor(y_va, dtype=torch.long)
    w_v = cpu_tensor(class_adjusted_weights(w_va, y_va, w_arr), dtype=torch.float32)

    best_val, best_state, pat = float("inf"), None, 0
    loss_history: list[dict] = []
    from app.services.bots.ml_early_stop import early_stop_patience, mark_early_stop

    max_patience = early_stop_patience(cfg)
    early_stop_meta: dict = {
        "early_stopped": False,
        "epochs_trained": 0,
        "epochs_budget": int(epochs),
        "early_stop_patience": max_patience,
    }

    train_loader = make_torch_dataloader(
        X_t, y_t, batch_size=batch_size, device=device, shuffle=True, w_t=w_t,
    )
    val_loader = make_torch_dataloader(
        X_v, y_v, batch_size=batch_size, device=device, shuffle=False, w_t=w_v,
    )
    for ep in range(epochs):
        if ml_cancel_requested(progress_path):
            return cancelled_train_result(symbol, "TRANSFORMER_SIGNAL")
        model.train()
        ep_loss = 0.0
        n_batches = 0
        for batch in train_loader:
            xb, yb, wb = unpack_batch_to_device(batch, device)
            optimizer.zero_grad()
            per = criterion(model(xb), yb)
            loss = (per * wb).mean()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            ep_loss += loss.item()
            n_batches += 1

        avg_train = ep_loss / max(1, n_batches)
        model.eval()
        with torch.no_grad():
            vl = 0.0
            n_v = 0.0
            for batch in val_loader:
                xb, yb, wb = unpack_batch_to_device(batch, device)
                per = criterion(model(xb), yb)
                vl += float((per * wb).sum().item())
                n_v += float(wb.sum().item())
            vl = vl / max(1.0, n_v)
        loss_history.append({
            "epoch": ep + 1,
            "train_loss": round(avg_train, 6),
            "val_loss": round(vl, 6),
        })
        write_ml_progress(
            progress_path,
            pct=min(20 + int(((ep + 1) / max(epochs, 1)) * 70), 90),
            phase="epoch",
            detail=f"{ep + 1}/{epochs}",
        )
        early_stop_meta["epochs_trained"] = ep + 1
        if vl < best_val:
            best_val = vl
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            pat = 0
        else:
            pat += 1
            if pat >= max_patience:
                early_stop_meta.update(mark_early_stop(
                    epoch_1based=ep + 1,
                    epochs_budget=epochs,
                    patience=max_patience,
                    progress_path=progress_path,
                    strategy="TRANSFORMER_SIGNAL",
                ))
                break

        job_id = str(cfg.get("job_id") or cfg.get("_ml_job_id") or "") or None
        if job_id and best_state is not None:
            try:
                from app.services.bots.ml_job_checkpoint import (
                    empty_epoch_checkpoint,
                    save_torch_epoch_checkpoint,
                )
                from app.services.bots.ml_job_store import save_ml_job_checkpoint

                save_torch_epoch_checkpoint(
                    job_id,
                    model_state=best_state,
                    epoch=ep + 1,
                    epochs_budget=epochs,
                    extra={"val_loss": float(vl)},
                )
                save_ml_job_checkpoint(
                    job_id,
                    {
                        **empty_epoch_checkpoint(
                            job_id=job_id,
                            strategy="TRANSFORMER_SIGNAL",
                            symbol=symbol,
                            epochs_budget=epochs,
                        ),
                        "last_epoch": ep + 1,
                        "val_loss": float(vl),
                    },
                )
            except Exception:
                pass

    if best_state:
        model.load_state_dict(best_state)
    model.eval()
    pred_chunks: list = []
    tr_pred_chunks: list = []
    with torch.no_grad():
        for vs in range(0, len(X_v), batch_size):
            xb = X_v[vs:vs + batch_size].to(device, non_blocking=True)
            pred_chunks.append(model(xb).argmax(1).detach().cpu())
        for ts in range(0, len(X_t), batch_size):
            xb = X_t[ts:ts + batch_size].to(device, non_blocking=True)
            tr_pred_chunks.append(model(xb).argmax(1).detach().cpu())

        va_pred = torch.cat(pred_chunks, dim=0).numpy() if pred_chunks else np.array([], dtype=np.int64)
        tr_pred = torch.cat(tr_pred_chunks, dim=0).numpy() if tr_pred_chunks else np.array([], dtype=np.int64)

    val_acc = float((va_pred == y_va).mean()) if len(y_va) > 0 else 0.0
    train_acc = float((tr_pred == y_tr).mean()) if len(y_tr) > 0 else 0.0
    gap = round(max(0.0, train_acc - val_acc), 4)

    # ONNX export — skip during walk-forward folds (export hangs the job at
    # epoch N/N and blocks job-status polls). OOS uses in-memory ``_wf_bundle``.
    train_device_meta = device_info(device)
    wf_mode = bool(cfg.get("_wf_mode") or cfg.get("wf_mode"))
    skip_onnx = bool(cfg.get("skip_onnx_export", wf_mode))

    if skip_onnx:
        write_ml_progress(progress_path, pct=92, phase="fold-train", detail="oos-ready")
        from app.services.bots.ml_torch_device import module_cpu_copy

        cpu_model = module_cpu_copy(model)
        meta = {
            "symbol": symbol, "timeframe": tf, "model_type": "transformer_signal",
            "feature_schema_version": SIGNAL_FEATURE_VERSION,
            "reverse_map": {str(k): v for k, v in REVERSE_MAP.items()},
            "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "metrics": {
                "train_accuracy": round(train_acc, 4),
                "val_accuracy": round(val_acc, 4),
                "overfitting_gap": gap,
                "val_loss": round(best_val, 4),
                "train_samples": int(len(y_tr)), "val_samples": int(len(y_va)),
                "train_device": train_device_meta.get("device"),
                **early_stop_meta,
            },
            "config": {
                "lookback": lookback, "d_model": d_model, "nhead": nhead,
                "num_layers": n_layers, "timeframe": tf,
                "epochs": int(epochs),
                "early_stop_patience": max_patience,
                "train_device": train_device_meta,
            },
            "train_device": train_device_meta,
            "loss_history": loss_history,
            "early_stopped": bool(early_stop_meta.get("early_stopped")),
            "epochs_trained": int(early_stop_meta.get("epochs_trained") or len(loss_history)),
        }
        return {
            "ok": True,
            "symbol": symbol,
            "timeframe": tf,
            **meta,
            "_wf_bundle": {
                "strategy": "TRANSFORMER_SIGNAL",
                "model": cpu_model,
                "mean": mean,
                "std": std,
                "lookback": lookback,
                "reverse_map": dict(REVERSE_MAP),
                "min_confidence": float(cfg.get("min_confidence", 0.55)),
            },
        }

    os.makedirs(_model_dir(symbol, tf), exist_ok=True)
    from app.services.bots.ml_model_artifacts import export_onnx_single_file

    write_ml_progress(progress_path, pct=92, phase="export", detail="onnx")
    export_onnx_single_file(
        model,
        torch.randn(1, lookback, N_FEATURES),
        _onnx_path(symbol, tf),
        input_names=["input"],
        output_names=["logits"],
        dynamic_axes={"input": {0: "b"}, "logits": {0: "b"}},
        opset_version=17,
        invalidate=lambda: _transformer_store.invalidate(symbol, timeframe=tf),
    )

    with open(_scaler_path(symbol, tf), "w") as f:
        from app.services.bots.ml_feature_v8 import attach_ffd_d

        json.dump(attach_ffd_d({"mean": mean.tolist(), "std": std.tolist()}), f, indent=2)

    # Persist the trainable checkpoint so other assets can warm-start from it.
    try:
        torch.save(model.state_dict(), _checkpoint_path(symbol, tf))
    except Exception:
        logger.debug("Transformer checkpoint.pt save failed for %s", symbol, exc_info=True)

    meta = {
        "symbol": symbol, "timeframe": tf, "model_type": "transformer_signal",
        "feature_schema_version": SIGNAL_FEATURE_VERSION,
        **feature_scheme_metadata(cfg),
        "reverse_map": {str(k): v for k, v in REVERSE_MAP.items()},
        "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "metrics": {
            "train_accuracy": round(train_acc, 4),
            "val_accuracy": round(val_acc, 4),
            "overfitting_gap": gap,
            "val_loss": round(best_val, 4),
            "train_samples": int(len(y_tr)), "val_samples": int(len(y_va)),
            "train_device": train_device_meta.get("device"),
            **early_stop_meta,
        },
        "config": {
            "lookback": lookback, "d_model": d_model, "nhead": nhead,
            "num_layers": n_layers, "timeframe": tf,
            "epochs": int(epochs),
            "early_stop_patience": max_patience,
            "train_device": train_device_meta,
            **event_filter_metadata(cfg),
            **feature_scheme_metadata(cfg),
        },
        "train_device": train_device_meta,
        "loss_history": loss_history,
        "early_stopped": bool(early_stop_meta.get("early_stopped")),
        "epochs_trained": int(early_stop_meta.get("epochs_trained") or len(loss_history)),
    }
    try:
        from app.services.bots.ml_feature_v8 import last_selected_ffd_d

        meta["frac_diff_d_ffd"] = last_selected_ffd_d()
    except Exception:
        pass
    if donor_lineage is not None:
        meta["transfer"] = donor_lineage
        meta["metrics"]["transfer"] = {
            "donor_symbol": donor_lineage.get("donor_symbol"),
            "donor_version_id": donor_lineage.get("donor_version_id"),
            "freeze_trunk": bool(
                (donor_lineage.get("finetune_budget") or {}).get("freeze_trunk")
            ),
        }
    tw = cfg.get("_training_window") if isinstance(cfg.get("_training_window"), dict) else None
    if tw:
        meta["training_window"] = tw
        meta["candle_bars"] = tw.get("bars")
        meta["bar_target"] = tw.get("bar_limit")
    with open(_metadata_path(symbol, tf), "w") as f:
        json.dump(meta, f, indent=2)

    _transformer_store.invalidate(symbol, timeframe=tf)
    skip_snapshot = bool(cfg.get("skip_snapshot", cfg.get("_wf_mode", False)))
    if not skip_snapshot:
        try:
            from app.services.bots.ml_model_artifacts import snapshot_current_version
            snap = snapshot_current_version(_model_dir(symbol, tf), strategy="TRANSFORMER_SIGNAL")
            if snap:
                meta["version_id"] = snap.get("version_id")
                meta["version_path"] = snap.get("path")
        except Exception:
            logger.exception("Failed to snapshot Transformer version for %s", symbol)
    return {"ok": True, "symbol": symbol, "timeframe": tf, **meta}


# ── Model store ───────────────────────────────────────────────────────────


class TransformerModelStore:
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
    def _cache_key(symbol, model_version, timeframe=None):
        from app.services.bots.ml_model_artifacts import model_storage_key

        return f"{model_storage_key(symbol, timeframe)}|{model_version or 'latest'}"

    @staticmethod
    def _session_key(
        symbol,
        model_version,
        timeframe=None,
        *,
        research: bool = False,
        config: dict | None = None,
    ) -> str:
        from app.services.bots.ml_onnx_runtime import ort_provider_cache_tag

        base = TransformerModelStore._cache_key(symbol, model_version, timeframe)
        return f"{base}{ort_provider_cache_tag(research=research, config=config)}"

    def invalidate(self, symbol=None, *, timeframe: str | None = None):
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
            for d in (self._sessions, self._metadata, self._scalers, self._mtime):
                d.clear()

    def predict(self, symbol, window, *, model_version=None, timeframe=None):
        key = self._session_key(symbol, model_version, timeframe)
        session = self._ensure_loaded(
            symbol, model_version=model_version, timeframe=timeframe,
        )
        if session is None:
            return None
        scaler = self._scalers.get(key)
        try:
            if scaler:
                from app.services.bots.ml_feature_engineering import apply_feature_scaler, mask_features_for_model

                meta = self._metadata.get(key, {})
                window = mask_features_for_model(window, meta)
                window = apply_feature_scaler(
                    window,
                    scaler["mean"],
                    scaler["std"],
                    log_label=f"Transformer[{symbol}]",
                )
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

    def predict_batch(
        self,
        symbol,
        windows: np.ndarray,
        *,
        model_version=None,
        timeframe=None,
        batch_size: int = 512,
        cancel_cb=None,
        research: bool = False,
        config: dict | None = None,
    ) -> list:
        """Batched ONNX inference — ``windows`` shape ``(N, seq, F)``.

        Default ``research=False`` keeps live CPU sessions; research sim modes
        pass ``research=True`` for optional CUDA.
        """
        n = int(windows.shape[0]) if windows is not None else 0
        if n == 0:
            return []
        key = self._session_key(
            symbol, model_version, timeframe, research=research, config=config,
        )
        session = self._ensure_loaded(
            symbol,
            model_version=model_version,
            timeframe=timeframe,
            research=research,
            config=config,
        )
        if session is None:
            return [None] * n
        scaler = self._scalers.get(key)
        meta = self._metadata.get(key) or {}
        rmap = meta.get("reverse_map") or REVERSE_MAP
        out: list = [None] * n
        bs = max(32, int(batch_size or 512))
        mean = std = None
        if scaler:
            mean = np.array(scaler["mean"], dtype=np.float32)
            std = np.array(scaler["std"], dtype=np.float32)
        from app.services.bots.ml_feature_engineering import (
            apply_feature_scaler,
            mask_features_for_model,
        )

        windows = mask_features_for_model(windows, meta)
        for start in range(0, n, bs):
            if cancel_cb is not None and cancel_cb():
                raise InterruptedError("ml_batch_cancel_requested")
            end = min(start + bs, n)
            chunk = windows[start:end].astype(np.float32)
            try:
                if mean is not None and std is not None:
                    chunk = apply_feature_scaler(
                        chunk, mean, std, log_label=f"Transformer[{symbol}]",
                    )
                logits = session.run(None, {"input": chunk})[0]
            except Exception as exc:
                logger.warning(
                    "Transformer batch predict failed for %s [%s:%s]: %s",
                    symbol, start, end, exc,
                )
                continue
            for j, row in enumerate(logits):
                x = row - row.max()
                proba = np.exp(x) / np.exp(x).sum()
                idx = int(np.argmax(proba))
                out[start + j] = (rmap.get(str(idx), "NONE"), float(proba[idx]))
        return out

    def get_metadata(
        self,
        symbol,
        model_version=None,
        *,
        timeframe=None,
    ):
        self._ensure_loaded(symbol, model_version=model_version, timeframe=timeframe)
        key = self._session_key(symbol, model_version, timeframe)
        meta = self._metadata.get(key)
        scaler = self._scalers.get(key) or {}
        if not meta and not scaler:
            return None
        out = dict(meta or {})
        if out.get("frac_diff_d_ffd") is None and scaler.get("frac_diff_d_ffd") is not None:
            out["frac_diff_d_ffd"] = scaler.get("frac_diff_d_ffd")
        return out

    def _ensure_loaded(
        self,
        symbol,
        model_version=None,
        *,
        timeframe=None,
        research: bool = False,
        config: dict | None = None,
    ):
        from app.services.bots.ml_model_artifacts import resolve_model_dir

        key = self._session_key(
            symbol, model_version, timeframe, research=research, config=config,
        )
        if key in self._sessions and self._mtime.get(key) == -1.0:
            self._lru.touch(key)
            return self._sessions[key]

        load_dir = resolve_model_dir(_model_dir(symbol, timeframe), model_version)
        path = os.path.join(load_dir, "transformer_signal.onnx")
        if not os.path.isfile(path):
            return None
        mt = os.path.getmtime(path)
        if key in self._sessions and self._mtime.get(key) == mt:
            self._lru.touch(key)
            return self._sessions[key]
        try:
            import onnxruntime  # noqa: F401
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
            from app.services.bots.ml_onnx_runtime import create_inference_session

            s = create_inference_session(path, research=research, config=config)
        except Exception:
            return None
        self._sessions[key] = s
        self._mtime[key] = mt
        self._lru.touch(key)
        return s


_transformer_store = TransformerModelStore()

def get_transformer_store():
    return _transformer_store
