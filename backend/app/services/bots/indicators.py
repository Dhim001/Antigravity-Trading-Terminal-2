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
        "regime_routing_enabled": False,
        "elevated_min_confidence": 0.65,
        "elevated_min_score": 3,
        "elevated_block_entries": False,
        "compressed_min_confidence": 0.55,
        "block_ranging_markets": False,  # 3.4-A: default disabled
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

    return out


def first_eval_index(_df: pd.DataFrame, _strategy: str, _config: dict | None) -> int:
    """First bar index to evaluate (closed-bar semantics, indicator warm-up)."""
    return max(1, MIN_WARMUP_BARS - 1)
