"""Built-in strategy metadata for API catalog and frontend picker."""

from __future__ import annotations

import os
from typing import Any

from app.config import ALLOW_CUSTOM_STRATEGIES, BASE_DIR
from app.services.bots.indicators import merge_strategy_config
from app.services.bots.tick_strategies import merge_tick_config
from app.services.bots.take_profit import merge_tp_config

_BAR_BUILTIN = [
    {
        "id": "MACD_RSI",
        "name": "MACD + RSI",
        "description": "MACD histogram crossover with RSI filter and ATR stops.",
        "category": "trend",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "BRS_SCALPING",
        "name": "Bollinger RSI Stochastic",
        "description": "Mean-reversion scalps at Bollinger bands with RSI/Stoch confirmation.",
        "category": "scalp",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "SUPERTREND_ADX",
        "name": "Supertrend + ADX",
        "description": "Trend following with Supertrend direction and ADX strength filter.",
        "category": "trend",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "VWAP_PULLBACK",
        "name": "VWAP Pullback",
        "description": "Pullback entries toward session VWAP in established trends.",
        "category": "intraday",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "CHART_AGENT",
        "name": "Chart Analyst Agent",
        "description": "Hybrid rule+LLM chart analysis; trades on high-confidence signals.",
        "category": "agent",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "ICT_SMC",
        "name": "ICT Smart Money Concepts",
        "description": "Order blocks, fair value gaps, and liquidity sweeps — popular SMC methodology.",
        "category": "smc",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "DONCHIAN_BREAKOUT",
        "name": "Donchian Breakout",
        "description": "Classic channel breakout with ATR expansion filter for momentum confirmation.",
        "category": "breakout",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "MARKET_MAKING",
        "name": "Market Making (Spread Capture)",
        "description": "Hummingbot-style spread capture with inventory skew management for crypto pairs.",
        "category": "market_making",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "CVD_DIVERGENCE",
        "name": "CVD Divergence",
        "description": "Detects divergence between price pivots and Cumulative Volume Delta to find hidden buying/selling.",
        "category": "microstructure",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "WYCKOFF_SPRING",
        "name": "Wyckoff Spring/Upthrust",
        "description": "Detects false breakdowns (Springs) and false breakouts (Upthrusts) using volume absorption.",
        "category": "smc",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "VPOC_REVERSION",
        "name": "Volume POC Reversion",
        "description": "Mean reversion towards the Volume Profile Point of Control from outside the Value Area.",
        "category": "intraday",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "ORDERFLOW_IMBALANCE",
        "name": "Order Flow Imbalance",
        "description": "Detects aggressive bid/ask pressure at the top of the orderbook.",
        "category": "microstructure",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "ABSORPTION_AGENT",
        "name": "Absorption Agent",
        "description": "Multi-domain bot that scores volume absorption and exhaustion to spot institutional footprints.",
        "category": "agent",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "ML_SIGNAL_BOOST",
        "name": "ML Signal Boost",
        "description": "Gradient-boosted tree signal classifier trained on triple-barrier labels. Learns non-linear patterns from OHLCV + indicators.",
        "category": "ml",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "LSTM_DIRECTION",
        "name": "LSTM Direction",
        "description": "Recurrent neural network trained on 60-bar sequences to predict price direction. Captures temporal patterns invisible to bar-by-bar strategies.",
        "category": "ml",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "RL_PPO_AGENT",
        "name": "RL Trading Agent",
        "description": "Deep reinforcement learning agent trained via Proximal Policy Optimization. Learns trading strategies through simulated market interaction.",
        "category": "ml",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "TCN_MULTI_HORIZON",
        "name": "TCN Multi-Horizon",
        "description": "Temporal convolutional network predicting 5/15/60-bar returns. Signals only when all horizons agree on direction.",
        "category": "ml",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "VAE_REGIME_DETECTOR",
        "name": "VAE Regime Detector",
        "description": "Variational autoencoder detecting anomalous market regimes via reconstruction error. Amplifies signals during breakouts, suppresses during instability. Also usable as a meta-gate on TA strategies.",
        "category": "ml",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "TRANSFORMER_SIGNAL",
        "name": "Transformer Signal",
        "description": "Lightweight Transformer encoder with self-attention over bar sequences. Learns which past bars are most relevant to current trading decisions.",
        "category": "ml",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "GNN_CROSS_ASSET",
        "name": "GNN Cross-Asset",
        "description": "Graph neural network propagating signals across correlated assets. Captures lead-lag relationships for multi-symbol trading.",
        "category": "ml",
        "execution_mode": "BAR_CLOSE",
    },
    {
        "id": "HYBRID_ENSEMBLE",
        "name": "Hybrid Ensemble",
        "description": "Weighted vote across TA + ML + RL (proposal §7). Fires when components agree with enough confidence; suppresses single-source noise.",
        "category": "ml",
        "execution_mode": "BAR_CLOSE",
    },
]

_TICK_BUILTIN = [
    {
        "id": "TICK_MOMENTUM",
        "name": "Tick Momentum",
        "description": "Enter on short rolling momentum bursts; exit on reversal.",
        "category": "tick",
        "execution_mode": "TICK",
    },
    {
        "id": "TICK_MEAN_REVERT",
        "name": "Tick Mean Reversion",
        "description": "Fade z-score spikes over a rolling tick window.",
        "category": "tick",
        "execution_mode": "TICK",
    },
    {
        "id": "TICK_BREAKOUT",
        "name": "Tick Breakout",
        "description": "Break prior tick-range high/low with cooldown.",
        "category": "tick",
        "execution_mode": "TICK",
    },
]


def _custom_modules() -> list[str]:
    strategies_dir = os.path.join(BASE_DIR, "strategies")
    if not ALLOW_CUSTOM_STRATEGIES or not os.path.isdir(strategies_dir):
        return []
    return sorted(
        f[:-3]
        for f in os.listdir(strategies_dir)
        if f.endswith(".py") and not f.startswith("_")
    )


def list_strategy_catalog() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in _BAR_BUILTIN:
        defaults = merge_strategy_config(item["id"], {})
        defaults = merge_tp_config(item["id"], defaults)
        out.append({**item, "defaults": defaults, "custom": False})
    for item in _TICK_BUILTIN:
        defaults = merge_tick_config(item["id"], {})
        defaults = merge_tp_config(item["id"], defaults)
        out.append({**item, "defaults": defaults, "custom": False})
    for module in _custom_modules():
        out.append({
            "id": "CUSTOM",
            "module": module,
            "name": f"Custom: {module}",
            "description": f"User plugin from strategies/{module}.py",
            "category": "custom",
            "execution_mode": "BAR_CLOSE",
            "defaults": merge_strategy_config("MACD_RSI", {"module": module}),
            "custom": True,
        })
    return out
