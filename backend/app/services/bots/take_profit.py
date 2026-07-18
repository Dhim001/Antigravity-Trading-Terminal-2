"""Take-profit resolution for bot entries — config, strategy targets, and validation."""

from __future__ import annotations

from typing import Any

STRATEGY_TP_DEFAULTS: dict[str, dict[str, Any]] = {
    "MACD_RSI": {"take_profit_percent": 3.0, "tp_mode": "percent"},
    "BRS_SCALPING": {"tp_mode": "strategy"},
    "SUPERTREND_ADX": {"take_profit_percent": 4.0, "tp_mode": "percent"},
    "VWAP_PULLBACK": {"take_profit_percent": 2.5, "tp_mode": "percent"},
    "TICK_MOMENTUM": {"take_profit_percent": 0.2, "tp_mode": "percent"},
    "TICK_MEAN_REVERT": {"take_profit_percent": 0.15, "tp_mode": "percent"},
    "TICK_BREAKOUT": {"take_profit_percent": 0.25, "tp_mode": "percent"},
    "CHART_AGENT": {"take_profit_percent": 3.0, "tp_mode": "percent"},
    "ABSORPTION_AGENT": {"take_profit_percent": 2.5, "tp_mode": "percent"},
    "CVD_DIVERGENCE": {"take_profit_percent": 2.5, "tp_mode": "percent"},
    "WYCKOFF_SPRING": {"take_profit_percent": 3.0, "tp_mode": "percent"},
    "VPOC_REVERSION": {"take_profit_percent": 2.0, "tp_mode": "percent"},
    "ORDERFLOW_IMBALANCE": {"take_profit_percent": 1.5, "tp_mode": "percent"},
    "ICT_SMC": {"take_profit_percent": 3.0, "tp_mode": "percent"},
    "DONCHIAN_BREAKOUT": {"take_profit_percent": 4.0, "tp_mode": "percent"},
    "MARKET_MAKING": {"tp_mode": "none"},
    "ML_SIGNAL_BOOST": {"take_profit_percent": 3.0, "tp_mode": "percent"},
    "LSTM_DIRECTION": {"take_profit_percent": 3.0, "tp_mode": "percent"},
    "RL_PPO_AGENT": {"take_profit_percent": 3.0, "tp_mode": "percent"},
    "TCN_MULTI_HORIZON": {"take_profit_percent": 2.5, "tp_mode": "percent"},
    "VAE_REGIME_DETECTOR": {"take_profit_percent": 2.5, "tp_mode": "percent"},
    "TRANSFORMER_SIGNAL": {"take_profit_percent": 3.0, "tp_mode": "percent"},
    "GNN_CROSS_ASSET": {"take_profit_percent": 3.0, "tp_mode": "percent"},
}


def merge_tp_config(strategy: str, config: dict | None) -> dict:
    """Merge strategy-specific TP defaults into bot config."""
    key = (strategy or "").upper()
    defaults = STRATEGY_TP_DEFAULTS.get(key, {})
    return {**defaults, **(config or {})}


def resolve_take_profit(
    config: dict,
    signal_data: dict,
    side: str,
    entry_price: float,
) -> tuple[float | None, float | None]:
    """
    Resolve take profit for an entry order.

    Returns (take_profit_percent, take_profit_price). When an absolute price is
    used, percent is derived for OMSes that only accept percent; sim OMS prefers
    the absolute price when provided.
    """
    if entry_price <= 0 or side not in ("BUY", "SELL"):
        return None, None

    tp_mode = (config.get("tp_mode") or "auto").lower()
    if tp_mode == "none":
        return None, None

    strategy_tp = signal_data.get("take_profit_price")
    config_tp_pct = config.get("take_profit_percent")
    config_tp_price = config.get("take_profit_price")

    tp_price: float | None = None
    tp_pct: float | None = None

    if tp_mode == "strategy" and strategy_tp is not None:
        tp_price = float(strategy_tp)
    elif tp_mode == "percent" and config_tp_pct is not None:
        tp_pct = float(config_tp_pct)
    elif config_tp_price is not None:
        tp_price = float(config_tp_price)
    elif strategy_tp is not None and tp_mode in ("strategy", "auto"):
        tp_price = float(strategy_tp)
    elif config_tp_pct is not None:
        tp_pct = float(config_tp_pct)

    if tp_price is not None:
        if side == "BUY" and tp_price <= entry_price:
            tp_price = None
        elif side == "SELL" and tp_price >= entry_price:
            tp_price = None
        else:
            tp_pct = round(abs(tp_price - entry_price) / entry_price * 100, 4)

    if tp_pct is not None and tp_price is None:
        if side == "BUY":
            tp_price = entry_price * (1 + tp_pct / 100)
        else:
            tp_price = entry_price * (1 - tp_pct / 100)

    return tp_pct, tp_price


def format_tp_summary(tp_pct: float | None, tp_price: float | None) -> str:
    if tp_price is not None:
        if tp_pct is not None:
            return f"TP {tp_price:.4f} ({tp_pct:.2f}%)"
        return f"TP {tp_price:.4f}"
    if tp_pct is not None:
        return f"TP {tp_pct:.2f}%"
    return "no TP"
