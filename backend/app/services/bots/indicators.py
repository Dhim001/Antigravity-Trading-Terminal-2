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
        # Catalog / deploy: Chart Agent emits both sides; LONG_ONLY would mute shorts.
        "direction_mode": "BOTH",
    },
    "CVD_DIVERGENCE": {
        "pivot_lookback": 5,
        "adx_length": 14,
        "adx_threshold": 40,
        "atr_length": 14,
    },
    "WYCKOFF_SPRING": {
        "range_lookback": 30,
        "atr_length": 14,
        "volume_surge_mult": 1.5,
    },
    "VPOC_REVERSION": {
        "profile_lookback": 100,
        "value_area_pct": 0.7,
        "rsi_length": 14,
        "adx_length": 14,
        "adx_trend_filter": 35,
        "atr_length": 14,
    },
    "ORDERFLOW_IMBALANCE": {
        "bair_threshold": 0.3,
        "mlofi_threshold": 0.2,
        "volume_surge_mult": 1.3,
        "volume_ma_length": 20,
        "rsi_length": 14,
        "rsi_overbought": 65,
        "rsi_oversold": 35,
        "atr_length": 14,
        "allow_candle_proxy": True,
        "book_levels": 5,
    },
    "ABSORPTION_AGENT": {
        "atr_length": 14,
        "volume_ma_length": 20,
    },
    "ML_SIGNAL_BOOST": {
        "rsi_length": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_length": 14,
        "adx_length": 14,
        "min_confidence": 0.55,
        "model_symbol": "",
        "model_version": "",
        "model_artifact": "",
        "retrain_interval_hours": 24,
        "triple_barrier_atr_mult": 2.0,
        "triple_barrier_max_bars": 30,
        "min_train_samples": 200,
        "val_fraction": 0.2,
        "direction_mode": "BOTH",
        # GBM architecture — GPU hosts still train HistGBM on CPU; larger trees help capacity.
        "gbm_max_depth": 6,
        "gbm_learning_rate": 0.05,
        "gbm_max_iter": 300,
        "gbm_l2_reg": 0.0,
        "wf_capacity_parity": True,
    },
    "LSTM_DIRECTION": {
        "rsi_length": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_length": 14,
        "adx_length": 14,
        # Directional P(BUY)/P(SELL) after ignoring NONE mass; 0.55 was too strict.
        "min_confidence": 0.40,
        "lookback": 90,
        "hidden_dim": 128,
        "num_layers": 2,
        "epochs": 100,
        "learning_rate": 0.001,
        "batch_size": 128,
        "model_symbol": "",
        "model_version": "",
        "model_artifact": "",
        "triple_barrier_atr_mult": 2.0,
        "triple_barrier_max_bars": 30,
        "min_train_samples": 500,
        "val_fraction": 0.2,
        "direction_mode": "BOTH",
    },
    "RL_PPO_AGENT": {
        "rsi_length": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_length": 14,
        "adx_length": 14,
        # 4-way softmax → uniform ≈0.25; require a modest edge above chance.
        "min_confidence": 0.28,
        "model_symbol": "",
        "model_version": "",
        "model_artifact": "",
        "gamma": 0.99,
        "gae_lambda": 0.95,
        "clip_epsilon": 0.2,
        "ppo_epochs": 10,
        "n_steps": 2048,
        "total_timesteps": 200000,
        "hidden_dim": 256,
        "learning_rate": 0.0003,
        "batch_size": 128,
        "vf_coef": 0.5,
        "ent_coef": 0.01,
        "max_grad_norm": 0.5,
        "direction_mode": "BOTH",
    },
    "TCN_MULTI_HORIZON": {
        "rsi_length": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_length": 14,
        "adx_length": 14,
        "lookback": 120,
        "hidden_dim": 128,
        "num_blocks": 6,
        "epochs": 100,
        "min_return": 0.001,
        "min_confidence": 0.002,
        "learning_rate": 0.001,
        "batch_size": 128,
        "model_symbol": "",
        "model_version": "",
        "model_artifact": "",
        "min_train_samples": 300,
        "val_fraction": 0.2,
        "direction_mode": "BOTH",
    },
    "VAE_REGIME_DETECTOR": {
        "rsi_length": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_length": 14,
        "adx_length": 14,
        "hidden_dim": 128,
        "latent_dim": 32,
        "epochs": 120,
        "anomaly_threshold": 2.0,
        "suppress_threshold": 3.5,
        "learning_rate": 0.001,
        "batch_size": 256,
        "model_symbol": "",
        "model_version": "",
        "model_artifact": "",
        "min_train_samples": 200,
        "val_fraction": 0.2,
        # Meta-layer: when used as primary strategy these are N/A;
        # other strategies set vae_regime_gate_enabled to opt in.
        "vae_regime_gate_enabled": False,
        "vae_regime_rotation_hint": False,
        "direction_mode": "BOTH",
    },
    "TRANSFORMER_SIGNAL": {
        "rsi_length": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_length": 14,
        "adx_length": 14,
        "lookback": 90,
        "d_model": 128,
        "nhead": 4,
        "num_layers": 6,
        "epochs": 80,
        "min_confidence": 0.55,
        "learning_rate": 0.0005,
        "batch_size": 128,
        "model_symbol": "",
        "model_version": "",
        "model_artifact": "",
        "triple_barrier_atr_mult": 2.0,
        "triple_barrier_max_bars": 30,
        "min_train_samples": 300,
        "val_fraction": 0.2,
        "direction_mode": "BOTH",
    },
    "GNN_CROSS_ASSET": {
        "rsi_length": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_length": 14,
        "adx_length": 14,
        "min_confidence": 0.55,
        "hidden_dim": 128,
        "n_heads": 4,
        "epochs": 60,
        "basket_id": "",
        "model_symbol": "",
        "model_version": "",
        "model_artifact": "",
        "min_corr": 0.3,
        "direction_mode": "BOTH",
    },
    "HYBRID_ENSEMBLE": {
        "rsi_length": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_length": 14,
        "adx_length": 14,
        "ta_strategy": "MACD_RSI",
        "ml_strategy": "ML_SIGNAL_BOOST",
        "rl_strategy": "RL_PPO_AGENT",
        "ensemble_weight_ta": 0.3,
        "ensemble_weight_ml": 0.4,
        "ensemble_weight_rl": 0.3,
        "ensemble_threshold": 0.5,
        "ensemble_require_agreement": False,
        "ensemble_adaptive_weights": None,
        "model_symbol": "",
        "direction_mode": "BOTH",
        "calibration_gate_enabled": False,
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
    elif key == "CVD_DIVERGENCE":
        hl_range = out["high"] - out["low"]
        buy_pct = (out["close"] - out["low"]) / hl_range.replace(0, 1)
        buy_vol = out["volume"] * buy_pct
        sell_vol = out["volume"] * (1 - buy_pct)
        out["delta"] = buy_vol - sell_vol
        out["cvd"] = out["delta"].cumsum()
    elif key == "WYCKOFF_SPRING":
        pass  # Everything will be done in the StrategyV2 state
    elif key == "VPOC_REVERSION":
        pass  # Vol profile handled in state
    elif key == "ORDERFLOW_IMBALANCE":
        # Candle proxy for BAIR/MLOFI when L2 is missing or synthetic/balanced.
        hl_range = (out["high"] - out["low"]).replace(0, pd.NA)
        buy_pct = ((out["close"] - out["low"]) / hl_range).fillna(0.5).clip(0.0, 1.0)
        out["delta"] = out["volume"] * (2.0 * buy_pct - 1.0)
        # Top-of-bar pressure proxy ∈ [-1, 1]
        out["ofi_bair_proxy"] = (2.0 * buy_pct - 1.0).clip(-1.0, 1.0)
        # Smoothed multi-bar confirmation (stand-in for deeper book)
        out["ofi_mlofi_proxy"] = (
            out["ofi_bair_proxy"].rolling(3, min_periods=1).mean().clip(-1.0, 1.0)
        )
        vol_ma = int(cfg.get("volume_ma_length", 20))
        out[f"volume_ma_{vol_ma}"] = out["volume"].rolling(vol_ma).mean().shift(1)
    elif key == "ABSORPTION_AGENT":
        vol_ma = int(cfg.get("volume_ma_length", 20))
        out[f"volume_ma_{vol_ma}"] = out["volume"].rolling(vol_ma).mean().shift(1)

    return out


def first_eval_index(_df: pd.DataFrame, _strategy: str, _config: dict | None) -> int:
    """First bar index to evaluate (closed-bar semantics, indicator warm-up)."""
    return max(1, MIN_WARMUP_BARS - 1)
