"""Tier 5 — indicator fingerprinting for selective sweep cache reuse."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.services.bots.indicators import merge_strategy_config

# Keys that affect execution / risk gates but not indicator column computation.
EXECUTION_ONLY_KEYS = frozenset({
    "trailing_stop_percent",
    "take_profit_percent",
    "stop_loss_percent",
    "allocation",
    "slippage_bps",
    "fee_bps",
    "min_confidence",
    "min_score",
    "sim_mode",
    "live_parity",
    "tp_mode",
    "chandelier_stop_enabled",
    "chandelier_multiplier",
    "calibration_gate_enabled",
    "calibration_min_samples",
    "calibration_min_wilson",
    "meta_label_model_mode",
    "meta_label_min_prob",
    "meta_label_min_train_samples",
    "meta_label_shadow_mode",
    "use_meta_label_sizing",
    "use_confidence_sizing",
    "regime_routing_enabled",
    "elevated_min_confidence",
    "elevated_min_score",
    "elevated_block_entries",
    "compressed_min_confidence",
    "require_trend_alignment",
    "block_elevated_vol",
    "block_ranging_markets",
    "sentiment_filter_enabled",
    "min_sentiment_score",
    "use_llm",
    "use_vol_sizing",
    "direction_mode",
    "timeframe",
    "candle_source",
    "_bot_id",
    "backtest_bot_id",
    "meta_label_walk_forward",
})


def indicator_config_subset(strategy: str, config: dict | None) -> dict[str, Any]:
    """Config slice that affects process_candles / prepare_strategy_df indicators."""
    merged = merge_strategy_config(strategy, config)
    return {k: v for k, v in sorted(merged.items()) if k not in EXECUTION_ONLY_KEYS}


def indicator_fingerprint(strategy: str, config: dict | None) -> str:
    """Stable hash — identical when only risk/execution params differ."""
    subset = indicator_config_subset(strategy, config)
    payload = json.dumps(subset, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def unique_indicator_configs(strategy: str, configs: list[dict]) -> list[dict]:
    """One representative config per distinct indicator fingerprint."""
    seen: dict[str, dict] = {}
    for cfg in configs:
        fp = indicator_fingerprint(strategy, cfg)
        if fp not in seen:
            seen[fp] = cfg
    return list(seen.values())
