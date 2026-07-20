"""Subsystem memory snapshot for /health (MEMORY_CENTRIC_REVIEW #25)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _store_entry_count(store: Any) -> int:
    for attr in ("_sessions", "_models", "_metadata"):
        d = getattr(store, attr, None)
        if isinstance(d, dict):
            return len(d)
    lru = getattr(store, "_lru", None)
    if lru is not None:
        try:
            return len(lru)
        except Exception:
            pass
    return 0


def ml_model_cache_snapshot() -> dict[str, Any]:
    """Count in-memory ML/ONNX sessions across strategy stores."""
    getters = [
        ("ml_signal", "app.services.bots.strategies_ml", "get_ml_signal_store"),
        ("lstm", "app.services.bots.strategies_lstm", "get_lstm_store"),
        ("ppo", "app.services.bots.rl_ppo_trainer", "get_ppo_store"),
        ("tcn", "app.services.bots.ml_tcn_trainer", "get_tcn_store"),
        ("vae", "app.services.bots.ml_vae_regime", "get_vae_store"),
        ("transformer", "app.services.bots.ml_transformer_trainer", "get_transformer_store"),
        ("gnn", "app.services.bots.ml_gnn_trainer", "get_gnn_store"),
        ("meta_label", "app.services.bots.meta_label_model", "get_meta_label_store"),
    ]
    by_store: dict[str, int] = {}
    total = 0
    for name, mod_name, fn_name in getters:
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            store = getattr(mod, fn_name)()
            n = _store_entry_count(store)
            by_store[name] = n
            total += n
        except Exception:
            by_store[name] = 0
    return {"total_entries": total, "by_store": by_store}


def memory_subsystem_snapshot(state: Any | None = None) -> dict[str, Any]:
    """Best-effort RAM subsystem accounting for Settings / health."""
    out: dict[str, Any] = {}

    try:
        out["ml_model_caches"] = ml_model_cache_snapshot()
    except Exception as exc:
        out["ml_model_caches"] = {"error": str(exc)}

    screener = None
    if state is not None:
        screener = getattr(state, "screener", None)
        if screener is None:
            bt = getattr(state, "backtester", None)
            screener = getattr(bt, "screener", None) if bt is not None else None
    if screener is not None and hasattr(screener, "cache_stats"):
        try:
            out["screener_cache"] = screener.cache_stats()
        except Exception as exc:
            out["screener_cache"] = {"error": str(exc)}

    try:
        from app.config import (
            ML_MODEL_CACHE_MAX,
            ML_MODEL_CACHE_TTL_SEC,
            ML_TRAIN_PROCESS_ISOLATION,
            ML_TRAIN_MAX_WORKERS,
            SCREENER_CACHE_MAX_ENTRIES,
            SCREENER_CACHE_MAX_MB,
            SQLITE_CACHE_KB,
        )

        out["budgets"] = {
            "ml_model_cache_max": ML_MODEL_CACHE_MAX,
            "ml_model_cache_ttl_sec": ML_MODEL_CACHE_TTL_SEC,
            "ml_train_process_isolation": ML_TRAIN_PROCESS_ISOLATION,
            "ml_train_max_workers": ML_TRAIN_MAX_WORKERS,
            "screener_cache_max_entries": SCREENER_CACHE_MAX_ENTRIES,
            "screener_cache_max_mb": SCREENER_CACHE_MAX_MB,
            "sqlite_cache_kb": SQLITE_CACHE_KB,
        }
    except Exception:
        pass

    try:
        from app.services.bots.ml_job_store import ml_job_counts

        out["ml_jobs"] = ml_job_counts()
    except Exception as exc:
        out["ml_jobs"] = {"error": str(exc)}

    return out
