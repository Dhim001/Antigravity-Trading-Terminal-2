"""VAE Regime Detector — Variational Autoencoder for market anomaly detection.

Learns the "normal" distribution of market behavior from bar features.
When reconstruction error spikes, signals a regime change. Acts as a
meta-strategy layer: amplifies signals during clean regimes, suppresses
entries during anomalous/unstable regimes.

Architecture:
    Encoder: Linear(34→64→32) → mu, log_var (latent=16)
    Decoder: Linear(16→32→64→34) → reconstructed features
    Total: ~25K params
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

VAE_MODEL_DIR = os.path.join(BASE_DIR, "data", "vae_regime_models")
N_FEATURES = len(SIGNAL_FEATURE_NAMES)
LATENT_DIM = 32


def _model_dir(symbol: str, timeframe: str | None = None) -> str:
    from app.services.bots.ml_model_artifacts import model_storage_key

    return os.path.join(VAE_MODEL_DIR, model_storage_key(symbol, timeframe))


def _onnx_path(symbol: str, timeframe: str | None = None) -> str:
    return os.path.join(_model_dir(symbol, timeframe), "vae_regime.onnx")


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
        raise RuntimeError("PyTorch required for VAE training") from exc


# ── VAE Model ─────────────────────────────────────────────────────────────


def _build_vae(input_dim: int = N_FEATURES, hidden_dim: int = 128,
               latent_dim: int = LATENT_DIM):
    """Build a Variational Autoencoder."""
    torch, nn = _get_torch()

    class VAE(nn.Module):
        def __init__(self):
            super().__init__()
            # Encoder
            self.enc1 = nn.Linear(input_dim, hidden_dim)
            self.enc2 = nn.Linear(hidden_dim, hidden_dim // 2)
            self.fc_mu = nn.Linear(hidden_dim // 2, latent_dim)
            self.fc_logvar = nn.Linear(hidden_dim // 2, latent_dim)
            # Decoder
            self.dec1 = nn.Linear(latent_dim, hidden_dim // 2)
            self.dec2 = nn.Linear(hidden_dim // 2, hidden_dim)
            self.dec3 = nn.Linear(hidden_dim, input_dim)
            self.relu = nn.ReLU()

        def encode(self, x):
            h = self.relu(self.enc1(x))
            h = self.relu(self.enc2(h))
            return self.fc_mu(h), self.fc_logvar(h)

        def reparameterize(self, mu, logvar):
            std = torch.exp(0.5 * logvar)
            eps = torch.randn_like(std)
            return mu + eps * std

        def decode(self, z):
            h = self.relu(self.dec1(z))
            h = self.relu(self.dec2(h))
            return self.dec3(h)

        def forward(self, x):
            mu, logvar = self.encode(x)
            z = self.reparameterize(mu, logvar)
            recon = self.decode(z)
            return recon, mu, logvar

    return VAE()


def vae_loss(recon_x, x, mu, logvar):
    """VAE loss = reconstruction (MSE) + KL divergence."""
    _, nn = _get_torch()
    recon_loss = nn.functional.mse_loss(recon_x, x, reduction="mean")
    kl_loss = -0.5 * (1 + logvar - mu.pow(2) - logvar.exp()).mean()
    return recon_loss + 0.1 * kl_loss, recon_loss, kl_loss


# ── Anomaly scoring ──────────────────────────────────────────────────────


def compute_reconstruction_error(original: np.ndarray, reconstructed: np.ndarray) -> float:
    """Per-sample MSE reconstruction error."""
    return float(np.mean((original - reconstructed) ** 2))


# ── Training pipeline ────────────────────────────────────────────────────


def train_vae_regime_model(
    symbol: str,
    candles: list[dict],
    *,
    config: dict | None = None,
    epochs: int = 120,
) -> dict[str, Any]:
    """Train a VAE on bar features to learn normal market distribution."""
    torch, nn = _get_torch()

    raw_cfg = dict(config or {})
    cfg = merge_strategy_config("VAE_REGIME_DETECTOR", raw_cfg)
    from app.services.bots.ml_model_artifacts import normalize_model_timeframe

    tf = normalize_model_timeframe(cfg.get("timeframe") or raw_cfg.get("timeframe"))
    cfg["timeframe"] = tf
    epochs = int(cfg.get("epochs", epochs))
    hidden_dim = int(cfg.get("hidden_dim", 128))
    latent_dim = int(cfg.get("latent_dim", LATENT_DIM))
    lr = float(cfg.get("learning_rate", 0.001))
    val_fraction = float(cfg.get("val_fraction", 0.2))
    min_samples = int(cfg.get("min_train_samples", 200))
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
    batch_size = suggest_batch_size(cfg, 128, device=device)
    ensure_cuda_ready(device)

    if len(candles) < 100:
        return {"ok": False, "error": "insufficient candles", "symbol": symbol}

    # Extract features for every bar
    feature_lb = 20
    vectors: list[np.ndarray] = []
    for i in range(feature_lb, len(candles)):
        c = candles[i]
        lb = candles[max(0, i - feature_lb):i]
        features = bar_to_signal_features(c, lookback_rows=lb)
        vectors.append(signal_features_to_vector(features))

    X = np.stack(vectors).astype(np.float32)
    n = len(X)
    if n < min_samples:
        return {"ok": False, "error": f"insufficient samples ({n})", "symbol": symbol}

    # Normalize
    feat_mean = X.mean(axis=0)
    feat_std = X.std(axis=0)
    feat_std = np.where(feat_std < 1e-8, 1.0, feat_std)
    X = (X - feat_mean) / feat_std

    # Split
    split = max(1, int(n * (1.0 - val_fraction)))
    X_train, X_val = X[:split], X[split:]

    model = _build_vae(
        input_dim=N_FEATURES, hidden_dim=hidden_dim, latent_dim=latent_dim,
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)

    X_t = cpu_tensor(X_train, dtype=torch.float32)
    X_v = cpu_tensor(X_val, dtype=torch.float32)

    best_val_loss = float("inf")
    best_state = None
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
            return cancelled_train_result(symbol, "VAE_REGIME_DETECTOR")
        model.train()
        indices = torch.randperm(len(X_t))
        epoch_loss = 0.0
        n_batches = 0
        for start in range(0, len(X_t), batch_size):
            idx = indices[start:start + batch_size]
            xb = X_t[idx].to(device, non_blocking=True)
            recon, mu, logvar = model(xb)
            loss, _, _ = vae_loss(recon, xb, mu, logvar)
            optimizer.zero_grad()
            loss.backward()
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
                v_recon, v_mu, v_logvar = model(xb)
                v_loss, _, _ = vae_loss(v_recon, xb, v_mu, v_logvar)
                val_loss += float(v_loss.item()) * len(xb)
                n_v += len(xb)
            val_loss = val_loss / max(1, n_v)

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

    if best_state:
        model.load_state_dict(best_state)

    # Compute baseline reconstruction error on training data
    model.eval()
    err_chunks: list = []
    with torch.no_grad():
        for vs in range(0, len(X_t), batch_size):
            xb = X_t[vs:vs + batch_size].to(device, non_blocking=True)
            train_recon, _, _ = model(xb)
            err_chunks.append(((train_recon - xb) ** 2).mean(dim=1).detach().cpu())
        train_errors = torch.cat(err_chunks, dim=0).numpy() if err_chunks else np.array([0.0])
        baseline_error = float(np.mean(train_errors))
        baseline_std = float(np.std(train_errors))

    # Export ONNX — forward pass returns (recon, mu, logvar) but ONNX needs single output
    # Export encoder+decoder as reconstruction-only model
    class ReconOnly(torch.nn.Module):
        def __init__(self, vae):
            super().__init__()
            self.vae = vae
        def forward(self, x):
            recon, _, _ = self.vae(x)
            return recon

    train_device_meta = device_info(device)
    recon_model = ReconOnly(model)
    os.makedirs(_model_dir(symbol, tf), exist_ok=True)
    from app.services.bots.ml_model_artifacts import export_onnx_single_file

    export_onnx_single_file(
        recon_model,
        torch.randn(1, N_FEATURES),
        _onnx_path(symbol, tf),
        input_names=["input"],
        output_names=["reconstruction"],
        dynamic_axes={"input": {0: "batch"}, "reconstruction": {0: "batch"}},
        opset_version=17,
        invalidate=lambda: get_vae_store().invalidate(symbol, timeframe=tf),
    )

    scaler = {"mean": feat_mean.tolist(), "std": feat_std.tolist()}
    with open(_scaler_path(symbol, tf), "w", encoding="utf-8") as fh:
        json.dump(scaler, fh, indent=2)

    metadata = {
        "symbol": symbol, "timeframe": tf, "model_type": "vae_regime",
        "feature_schema_version": SIGNAL_FEATURE_VERSION,
        "baseline_error": baseline_error,
        "baseline_std": baseline_std,
        "latent_dim": latent_dim,
        "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "metrics": {
            "train_samples": int(len(X_train)),
            "val_samples": int(len(X_val)),
            "val_loss": round(best_val_loss, 6),
            "baseline_recon_error": round(baseline_error, 6),
            "baseline_recon_std": round(baseline_std, 6),
            "train_device": train_device_meta.get("device"),
        },
        "loss_history": loss_history,
        "config": {
            "latent_dim": latent_dim,
            "hidden_dim": hidden_dim,
            "timeframe": tf,
            "train_device": train_device_meta,
        },
        "train_device": train_device_meta,
    }
    with open(_metadata_path(symbol, tf), "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)

    _vae_store.invalidate(symbol, timeframe=tf)
    skip_snapshot = bool(cfg.get("skip_snapshot", cfg.get("_wf_mode", False)))
    if not skip_snapshot:
        try:
            from app.services.bots.ml_model_artifacts import snapshot_current_version
            snap = snapshot_current_version(_model_dir(symbol, tf), strategy="VAE_REGIME_DETECTOR")
            if snap:
                metadata["version_id"] = snap.get("version_id")
                metadata["version_path"] = snap.get("path")
        except Exception:
            logger.exception("Failed to snapshot VAE version for %s", symbol)
    logger.info("VAE regime model trained for %s @ %s (n=%d, baseline_err=%.6f)", symbol, tf, n, baseline_error)
    return {"ok": True, "symbol": symbol, "timeframe": tf, **metadata}


# ── Model store ───────────────────────────────────────────────────────────


class VaeModelStore:
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

    def anomaly_score(
        self,
        symbol: str,
        features: np.ndarray,
        *,
        model_version: str | None = None,
        timeframe: str | None = None,
    ) -> float | None:
        """Compute anomaly score = reconstruction_error / baseline_error.

        Returns float (1.0 = normal, >2.0 = anomalous) or None.
        """
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
            features = (features.astype(np.float32) - mean) / std

        try:
            recon = session.run(None, {"input": features.reshape(1, -1).astype(np.float32)})[0][0]
            error = float(np.mean((features.flatten() - recon) ** 2))
            meta = self._metadata.get(key, {})
            baseline = float(meta.get("baseline_error", 1.0))
            if baseline < 1e-8:
                baseline = 1.0
            return error / baseline
        except Exception as exc:
            logger.warning("VAE anomaly score failed for %s: %s", symbol, exc)
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
        path = os.path.join(load_dir, "vae_regime.onnx")
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
            logger.warning("VAE load failed for %s: %s", key, exc)
            return None
        self._sessions[key] = session
        self._mtime[key] = mtime
        self._lru.touch(key)
        return session


_vae_store = VaeModelStore()

def get_vae_store() -> VaeModelStore:
    return _vae_store
