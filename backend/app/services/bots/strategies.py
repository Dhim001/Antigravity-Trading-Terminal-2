from app.services.bots.indicators import (
    adx_col,
    atr_col,
    bb_lower_col,
    bb_mid_col,
    bb_upper_col,
    macd_hist_col,
    merge_strategy_config,
    rsi_col,
    stoch_k_col,
    supertrend_dir_col,
    supertrend_val_col,
)


class BaseStrategy:
    def __init__(self, config: dict):
        self.config = config

    def evaluate(self, df_row) -> dict:
        """
        Takes the latest row of a pandas DataFrame (with indicators attached).
        Returns a dict: {'signal': 'BUY'|'SELL'|'CLOSE'|'NONE', 'stop_loss_price': float}
        """
        return {"signal": "NONE"}


class MacdRsiStrategy(BaseStrategy):
    """Strategy 1: MACD + RSI + Mean Reversion"""

    def evaluate(self, df_row) -> dict:
        try:
            cfg = merge_strategy_config("MACD_RSI", self.config)
            hist_col = macd_hist_col(cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"])
            macd_hist = df_row.get(hist_col, 0)
            macd_hist_prev = df_row.get(f"{hist_col}_prev", 0)
            rsi = df_row.get(rsi_col(cfg["rsi_length"]), 50)
            atr = df_row.get(atr_col(cfg["atr_length"]), 0)

            if macd_hist > 0 and macd_hist_prev <= 0 and rsi < 50:
                return {"signal": "BUY", "stop_loss_distance": 1.5 * atr}

            if macd_hist < 0 and macd_hist_prev >= 0 and rsi > 50:
                return {"signal": "SELL", "stop_loss_distance": 1.5 * atr}
        except Exception:
            pass
        return {"signal": "NONE"}


class BrsScalpingStrategy(BaseStrategy):
    """Strategy 2: Bollinger, RSI, Stochastic"""

    def evaluate(self, df_row) -> dict:
        try:
            cfg = merge_strategy_config("BRS_SCALPING", self.config)
            bbl = df_row.get(bb_lower_col(cfg["bb_length"], cfg["bb_std"]))
            bbu = df_row.get(bb_upper_col(cfg["bb_length"], cfg["bb_std"]))
            bbm = df_row.get(bb_mid_col(cfg["bb_length"], cfg["bb_std"]))
            rsi = df_row.get(rsi_col(cfg["rsi_length"]))
            stoch_k = df_row.get(
                stoch_k_col(cfg["stoch_k"], cfg["stoch_d"], cfg["stoch_smooth"])
            )
            close = df_row.get("close")
            atr = df_row.get(atr_col(cfg["atr_length"]))

            if None in (bbl, bbu, rsi, stoch_k, close):
                return {"signal": "NONE"}

            if (
                close < bbl
                and rsi < cfg["rsi_oversold"]
                and stoch_k < cfg["stoch_oversold"]
            ):
                return {
                    "signal": "BUY",
                    "stop_loss_distance": 1.5 * atr,
                    "take_profit_price": bbm,
                }

            if (
                close > bbu
                and rsi > cfg["rsi_overbought"]
                and stoch_k > cfg["stoch_overbought"]
            ):
                return {
                    "signal": "SELL",
                    "stop_loss_distance": 1.5 * atr,
                    "take_profit_price": bbm,
                }
        except Exception:
            pass
        return {"signal": "NONE"}


class SupertrendAdxStrategy(BaseStrategy):
    """Strategy 3: SuperTrend + ADX"""

    def evaluate(self, df_row) -> dict:
        try:
            cfg = merge_strategy_config("SUPERTREND_ADX", self.config)
            st_dir_col = supertrend_dir_col(cfg["st_length"], cfg["st_multiplier"])
            st_val_col = supertrend_val_col(cfg["st_length"], cfg["st_multiplier"])
            st_dir = df_row.get(st_dir_col)
            st_dir_prev = df_row.get(f"{st_dir_col}_prev")
            st_val = df_row.get(st_val_col)
            adx = df_row.get(adx_col(cfg["adx_length"]))

            if None in (st_dir, st_dir_prev, adx):
                return {"signal": "NONE"}

            threshold = cfg["adx_threshold"]
            if st_dir == 1 and st_dir_prev == -1 and adx > threshold:
                return {"signal": "BUY", "stop_loss_price": st_val}

            if st_dir == -1 and st_dir_prev == 1 and adx > threshold:
                return {"signal": "SELL", "stop_loss_price": st_val}
        except Exception:
            pass
        return {"signal": "NONE"}


class VwapPullbackStrategy(BaseStrategy):
    """Strategy 4: VWAP Pullback"""

    def evaluate(self, df_row) -> dict:
        try:
            cfg = merge_strategy_config("VWAP_PULLBACK", self.config)
            vwap = df_row.get("VWAP")
            close = df_row.get("close")
            close_prev = df_row.get("close_prev")
            atr = df_row.get(atr_col(cfg["atr_length"]))

            if None in (vwap, close, close_prev):
                return {"signal": "NONE"}

            if close_prev > vwap and close <= vwap:
                return {"signal": "BUY", "stop_loss_distance": 1.5 * atr}

            if close_prev < vwap and close >= vwap:
                return {"signal": "SELL", "stop_loss_distance": 1.5 * atr}
        except Exception:
            pass
        return {"signal": "NONE"}


_STRATEGY_ALIASES = {
    "SUPERTREND": "SUPERTREND_ADX",
    "BB_STOCH": "BRS_SCALPING",
}


def normalize_strategy_name(strategy_name: str) -> str:
    return _STRATEGY_ALIASES.get(strategy_name.upper(), strategy_name.upper())


def get_strategy(strategy_name: str, config: dict) -> BaseStrategy:
    strategies = {
        "MACD_RSI": MacdRsiStrategy,
        "BRS_SCALPING": BrsScalpingStrategy,
        "SUPERTREND_ADX": SupertrendAdxStrategy,
        "VWAP_PULLBACK": VwapPullbackStrategy,
    }
    key = normalize_strategy_name(strategy_name)

    if key == "CUSTOM":
        from app.services.bots.custom_loader import get_custom_strategy

        module = (config or {}).get("module", "example_rsi_reversal")
        custom = get_custom_strategy(module, config or {})
        if custom:
            return custom
        return BaseStrategy(config or {})

    strat_class = strategies.get(key, BaseStrategy)
    return strat_class(config)
