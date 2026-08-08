"""Single source of truth for Lab ML strategies, artifact layout, and trainers.

Consumers (HTTP train/status, train executor, walk-forward, sweep, backtest
metrics, retrain scheduler) should import from here instead of maintaining
parallel frozensets / subdir maps / trainer import tables.
"""

from __future__ import annotations

import importlib
import logging
from types import MappingProxyType
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

ML_STRATEGIES = frozenset({
    "ML_SIGNAL_BOOST",
    "LSTM_DIRECTION",
    "RL_PPO_AGENT",
    "TCN_MULTI_HORIZON",
    "VAE_REGIME_DETECTOR",
    "TRANSFORMER_SIGNAL",
    "GNN_CROSS_ASSET",
})

ENSEMBLE_STRATEGIES = frozenset({"HYBRID_ENSEMBLE"})

# Strategy → on-disk folder under backend/data/
_MODEL_SUBDIRS: dict[str, str] = {
    "ML_SIGNAL_BOOST": "ml_signal_models",
    "LSTM_DIRECTION": "lstm_signal_models",
    "RL_PPO_AGENT": "rl_ppo_models",
    "TCN_MULTI_HORIZON": "tcn_signal_models",
    "VAE_REGIME_DETECTOR": "vae_regime_models",
    "TRANSFORMER_SIGNAL": "transformer_signal_models",
    "GNN_CROSS_ASSET": "gnn_signal_models",
}
MODEL_SUBDIRS: Mapping[str, str] = MappingProxyType(_MODEL_SUBDIRS)

# Strategy → artifact filenames expected under model root
_STRATEGY_ARTIFACTS: dict[str, list[str]] = {
    "ML_SIGNAL_BOOST": ["model.joblib", "metadata.json"],
    "LSTM_DIRECTION": ["lstm_direction.onnx", "scaler.json", "metadata.json"],
    "RL_PPO_AGENT": ["ppo_policy.onnx", "scaler.json", "metadata.json"],
    "TCN_MULTI_HORIZON": ["tcn_multi_horizon.onnx", "scaler.json", "metadata.json"],
    "VAE_REGIME_DETECTOR": ["vae_regime.onnx", "scaler.json", "metadata.json"],
    "TRANSFORMER_SIGNAL": ["transformer_signal.onnx", "scaler.json", "metadata.json"],
    "GNN_CROSS_ASSET": ["gnn_cross_asset.onnx", "scaler.json", "metadata.json"],
}
STRATEGY_ARTIFACTS: Mapping[str, list[str]] = MappingProxyType({
    k: list(v) for k, v in _STRATEGY_ARTIFACTS.items()
})

# Canonical model_type strings written into metadata / status API
MODEL_TYPE_LABELS: Mapping[str, str] = MappingProxyType({
    "ML_SIGNAL_BOOST": "ml_signal_boost",  # HistGradientBoostingClassifier
    "LSTM_DIRECTION": "lstm_direction",
    "RL_PPO_AGENT": "rl_ppo",
    "TCN_MULTI_HORIZON": "tcn_multi_horizon",
    "VAE_REGIME_DETECTOR": "vae_regime",
    "TRANSFORMER_SIGNAL": "transformer_signal",
    "GNN_CROSS_ASSET": "gnn_cross_asset",
})

# strategy → (module_path, callable_name) — sole trainer import map
TRAINER_IMPORTS: Mapping[str, tuple[str, str]] = MappingProxyType({
    "ML_SIGNAL_BOOST": ("app.services.bots.strategies_ml", "train_ml_signal_model"),
    "LSTM_DIRECTION": ("app.services.bots.ml_lstm_trainer", "train_lstm_signal_model"),
    "RL_PPO_AGENT": ("app.services.bots.rl_ppo_trainer", "train_ppo_agent"),
    "TCN_MULTI_HORIZON": ("app.services.bots.ml_tcn_trainer", "train_tcn_model"),
    "VAE_REGIME_DETECTOR": ("app.services.bots.ml_vae_regime", "train_vae_regime_model"),
    "TRANSFORMER_SIGNAL": ("app.services.bots.ml_transformer_trainer", "train_transformer_model"),
    "GNN_CROSS_ASSET": ("app.services.bots.ml_gnn_trainer", "train_gnn_model"),
})

# Optuna auto-tune supports the full Lab ML set today
SWEEPABLE_ML_STRATEGIES = frozenset(ML_STRATEGIES)

_trainer_cache: dict[str, Callable[..., Any]] = {}


def is_ml_strategy(strategy: str) -> bool:
    """True for train/validate artifact strategies (not hybrid ensemble)."""
    return str(strategy or "").upper() in ML_STRATEGIES


def is_ensemble_strategy(strategy: str) -> bool:
    return str(strategy or "").upper() in ENSEMBLE_STRATEGIES


def model_type_label(strategy: str) -> str:
    key = str(strategy or "").upper()
    return MODEL_TYPE_LABELS.get(key) or key.lower()


def primary_artifact_name(strategy: str) -> str | None:
    """First non-metadata artifact for a strategy (joblib / onnx)."""
    arts = STRATEGY_ARTIFACTS.get(str(strategy or "").upper()) or []
    for name in arts:
        if name != "metadata.json":
            return name
    return None


def get_trainer_import(strategy: str) -> tuple[str, str] | None:
    return TRAINER_IMPORTS.get(str(strategy or "").upper())


def get_trainer(strategy: str) -> Callable[..., Any] | None:
    """Lazy-import the trainer callable for a strategy."""
    key = str(strategy or "").upper()
    if key in _trainer_cache:
        return _trainer_cache[key]
    entry = TRAINER_IMPORTS.get(key)
    if not entry:
        return None
    mod_name, fn_name = entry
    try:
        mod = importlib.import_module(mod_name)
        fn = getattr(mod, fn_name)
    except Exception:
        logger.debug("Trainer import failed for %s (%s.%s)", key, mod_name, fn_name, exc_info=True)
        return None
    _trainer_cache[key] = fn
    return fn
