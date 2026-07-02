"""Strategy config defaults, pandas-ta column naming, and evaluation prep."""

from __future__ import annotations

import pandas as pd

STRATEGY_DEFAULTS: dict[str, dict] = {
    "MACD_RSI": {
        "rsi_length": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_length": 14,
    },
    "BRS_SCALPING": {
        "bb_length": 20,
        "bb_std": 2.0,
        "rsi_length": 14,
        "stoch_k": 14,
        "stoch_d": 3,
        "stoch_smooth": 3,
        "atr_length": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "stoch_oversold": 20,
        "stoch_overbought": 80,
    },
    "SUPERTREND_ADX": {
        "st_length": 14,
        "st_multiplier": 3.0,
        "adx_length": 14,
        "adx_threshold": 25,
        "block_elevated_vol": False,  # 3.2-B: default block disabled
        "atr_length": 14,
    },
    "VWAP_PULLBACK": {
        "atr_length": 14,
        "rsi_length": 14,
        "use_rsi_confirmation": True,  # 3.2-C: default enabled
        "rsi_overbought_gate": 60,
        "rsi_oversold_gate": 40,
    },
    "ICT_SMC": {
        "ob_lookback": 10,
        "fvg_min_gap_pct": 0.0005,
        "sweep_lookback": 20,
        "atr_length": 14,
    },
    "DONCHIAN_BREAKOUT": {
        "breakout_length": 20,
        "exit_length": 10,
        "atr_confirm_mult": 1.0,
        "atr_length": 14,
    },
    "MARKET_MAKING": {
        "spread_pct": 0.002,
        "inventory_target": 0.0,
        "max_skew": 0.5,
        "atr_length": 14,
        "vol_shutdown_mult": 2.5,
    },
    "CHART_AGENT": {
        "rsi_length": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_length": 14,
        "adx_length": 14,
        "min_confidence": 0.55,
        "use_llm": False,
        "use_vol_sizing": True,
        "require_trend_alignment": False,
        "block_elevated_vol": False,
        "confirm_timeframe": "",
        "calibration_gate_enabled": False,
        "calibration_min_samples": 5,
        "calibration_min_wilson": 0.45,
        "meta_label_model_mode": "wilson",
        "meta_label_min_prob": 0.52,
        "meta_label_min_train_samples": 30,
        "meta_label_shadow_mode": False,
        "use_meta_label_sizing": False,
        "use_confidence_sizing": True,
        "regime_routing_enabled": False,
        "elevated_min_confidence": 0.65,
        "elevated_min_score": 3,
        "elevated_block_entries": False,
        "compressed_min_confidence": 0.55,
        "block_ranging_markets": False,  # 3.4-A: default disabled
        "sentiment_filter_enabled": False,
        "min_sentiment_score": 0.0,
    },
}

MIN_WARMUP_BARS = 50


def merge_strategy_config(strategy: str, config: dict | None) -> dict:
    key = strategy.upper()
    defaults = STRATEGY_DEFAULTS.get(key, {})
    return {**defaults, **(config or {})}


def _fmt_band_std(std: float) -> str:
    return f"{float(std):.1f}"


def _fmt_multiplier(mult: float) -> str:
    val = float(mult)
    return str(int(val)) if val == int(val) else str(val)


def macd_hist_col(fast: int, slow: int, signal: int) -> str:
    return f"MACDh_{fast}_{slow}_{signal}"


def rsi_col(length: int) -> str:
    return f"RSI_{length}"


def atr_col(length: int) -> str:
    return f"ATR_{length}"


def bb_lower_col(length: int, std: float) -> str:
    return f"BBL_{length}_{_fmt_band_std(std)}"


def bb_upper_col(length: int, std: float) -> str:
    return f"BBU_{length}_{_fmt_band_std(std)}"


def bb_mid_col(length: int, std: float) -> str:
    return f"BBM_{length}_{_fmt_band_std(std)}"


def stoch_k_col(k: int, d: int, smooth: int) -> str:
    return f"STOCHk_{k}_{d}_{smooth}"


def supertrend_dir_col(length: int, multiplier: float) -> str:
    return f"SUPERTd_{length}_{_fmt_multiplier(multiplier)}"


def supertrend_val_col(length: int, multiplier: float) -> str:
    return f"SUPERT_{length}_{_fmt_multiplier(multiplier)}"


def adx_col(length: int) -> str:
    return f"ADX_{length}"


def config_cache_key(strategy: str, config: dict | None) -> tuple:
    merged = merge_strategy_config(strategy, config)
    return tuple(sorted(merged.items()))


def prepare_strategy_df(df: pd.DataFrame, strategy: str, config: dict | None) -> pd.DataFrame:
    """Add *_prev columns required by the strategy evaluator."""
    if df.empty:
        return df

    cfg = merge_strategy_config(strategy, config)
    out = df.copy()
    key = strategy.upper()

    if key == "MACD_RSI":
        hist = macd_hist_col(cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"])
        if hist in out.columns:
            out[f"{hist}_prev"] = out[hist].shift(1)
    elif key == "BRS_SCALPING":
        pass
    elif key == "SUPERTREND_ADX":
        st_dir = supertrend_dir_col(cfg["st_length"], cfg["st_multiplier"])
        if st_dir in out.columns:
            out[f"{st_dir}_prev"] = out[st_dir].shift(1)
    elif key == "VWAP_PULLBACK":
        out["close_prev"] = out["close"].shift(1)
    elif key == "ICT_SMC":
        # OB detection needs prior bar open/close
        out["close_prev"] = out["close"].shift(1)
        out["open_prev"] = out["open"].shift(1)
        # FVG detection needs bar[-2] high and low
        out["prev2_high"] = out["high"].shift(2)
        out["prev2_low"] = out["low"].shift(2)
        out["prev2_low"] = out["low"].shift(2)
        # Liquidity sweep needs rolling high/low
        sweep_lb = int(cfg.get("sweep_lookback", 20))
        out[f"rolling_low_{sweep_lb}"] = out["low"].rolling(sweep_lb).min().shift(1)
        out[f"rolling_high_{sweep_lb}"] = out["high"].rolling(sweep_lb).max().shift(1)
    elif key == "DONCHIAN_BREAKOUT":
        breakout_len = int(cfg.get("breakout_length", 20))
        exit_len = int(cfg.get("exit_length", 10))
        # Donchian channels: rolling high/low (shifted by 1 to avoid lookahead)
        out[f"dc_high_{breakout_len}"] = out["high"].rolling(breakout_len).max().shift(1)
        out[f"dc_low_{breakout_len}"] = out["low"].rolling(breakout_len).min().shift(1)
        if exit_len != breakout_len:
            out[f"dc_high_{exit_len}"] = out["high"].rolling(exit_len).max().shift(1)
            out[f"dc_low_{exit_len}"] = out["low"].rolling(exit_len).min().shift(1)
    elif key == "MARKET_MAKING":
        pass  # Market making uses only standard OHLC + ATR columns

    return out


def first_eval_index(_df: pd.DataFrame, _strategy: str, _config: dict | None) -> int:
    """First bar index to evaluate (closed-bar semantics, indicator warm-up)."""
    return max(1, MIN_WARMUP_BARS - 1)
