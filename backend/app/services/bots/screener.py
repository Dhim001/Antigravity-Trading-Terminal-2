import importlib.metadata  # noqa: F401 — pandas can shadow importlib on Py 3.14

import logging

import pandas as pd
import pandas_ta as ta

from app.services.bots.indicators import (
    atr_col,
    config_cache_key,
    merge_strategy_config,
    rsi_col,
)


class MarketScreenerService:
    """
    Calculates technical indicators for bot strategies using pandas-ta.
    Indicator periods come from the bot config (with strategy defaults).
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._cache: dict[tuple, pd.DataFrame] = {}

    def process_candles(
        self,
        symbol: str,
        ohlcv_data: list,
        config: dict | None = None,
        strategy: str | None = None,
        *,
        full_history: bool = False,
    ) -> pd.DataFrame:
        """
        Converts candle dicts to a DataFrame and computes indicators for the strategy.
        Live signals use a rolling tail window; backtests pass full_history=True for the
        entire resolved candle series.
        """
        if not ohlcv_data or len(ohlcv_data) < 50:
            return pd.DataFrame()

        bar_time = ohlcv_data[-1].get("time")
        strat_key = (strategy or "MACD_RSI").upper()
        if strat_key == "CUSTOM":
            strat_key = (config or {}).get("base_strategy", "MACD_RSI").upper()
        cfg = merge_strategy_config(strat_key, config)
        cache_key = (symbol, bar_time, config_cache_key(strat_key, config))

        if bar_time is not None and not full_history:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached.copy()

        window = ohlcv_data if full_history else (
            ohlcv_data[-300:] if len(ohlcv_data) > 300 else ohlcv_data
        )
        df = pd.DataFrame(window)

        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["open", "high", "low", "close"])

        if len(df) < 50:
            return pd.DataFrame()

        try:
            self._compute_for_strategy(df, strat_key, cfg)
        except Exception as e:
            self.logger.error(f"Error calculating indicators for {symbol}: {e}")

        if bar_time is not None and not df.empty and not full_history:
            self._cache[cache_key] = df.copy()
            stale = [k for k in self._cache if k[0] == symbol and k[1] != bar_time]
            for key in stale:
                del self._cache[key]

        return df

    def _compute_for_strategy(self, df: pd.DataFrame, strategy: str, cfg: dict) -> None:
        """Compute indicator columns needed by the given strategy."""
        strat = strategy.upper()

        if strat in ("MACD_RSI", "BRS_SCALPING", "VWAP_PULLBACK", "SUPERTREND_ADX"):
            atr_len = cfg.get("atr_length", 14)
            atr_name = atr_col(atr_len)
            df[atr_name] = ta.atr(df["high"], df["low"], df["close"], length=atr_len)
            # 3.2-B: compute rolling median ATR to support regime-aware exit and sizing gates in any strategy
            df[f"{atr_name}_median_20"] = df[atr_name].rolling(window=20, min_periods=1).median()

        if strat in ("MACD_RSI",):
            macd = ta.macd(
                df["close"],
                fast=cfg["macd_fast"],
                slow=cfg["macd_slow"],
                signal=cfg["macd_signal"],
            )
            if macd is not None:
                for col in macd.columns:
                    df[col] = macd[col]
            df[rsi_col(cfg["rsi_length"])] = ta.rsi(df["close"], length=cfg["rsi_length"])

        if strat == "BRS_SCALPING":
            stoch = ta.stoch(
                df["high"],
                df["low"],
                df["close"],
                k=cfg["stoch_k"],
                d=cfg["stoch_d"],
                smooth_k=cfg["stoch_smooth"],
            )
            if stoch is not None:
                for col in stoch.columns:
                    df[col] = stoch[col]
            bb = ta.bbands(df["close"], length=cfg["bb_length"], std=cfg["bb_std"])
            if bb is not None:
                for col in bb.columns:
                    df[col] = bb[col]
            df[rsi_col(cfg["rsi_length"])] = ta.rsi(df["close"], length=cfg["rsi_length"])

        if strat == "SUPERTREND_ADX":
            st = ta.supertrend(
                df["high"],
                df["low"],
                df["close"],
                length=cfg["st_length"],
                multiplier=cfg["st_multiplier"],
            )
            if st is not None:
                for col in st.columns:
                    df[col] = st[col]
            adx = ta.adx(
                df["high"],
                df["low"],
                df["close"],
                length=cfg["adx_length"],
            )
            if adx is not None:
                for col in adx.columns:
                    df[col] = adx[col]

        if strat == "VWAP_PULLBACK":
            self._compute_vwap(df)
            # 3.2-C: Compute RSI for VWAP pullback confirmation filter
            rsi_len = cfg.get("rsi_length", 14)
            df[rsi_col(rsi_len)] = ta.rsi(df["close"], length=rsi_len)

        if strat == "CHART_AGENT":
            macd = ta.macd(
                df["close"],
                fast=cfg.get("macd_fast", 12),
                slow=cfg.get("macd_slow", 26),
                signal=cfg.get("macd_signal", 9),
            )
            if macd is not None:
                for col in macd.columns:
                    df[col] = macd[col]
            df[rsi_col(cfg.get("rsi_length", 14))] = ta.rsi(
                df["close"], length=cfg.get("rsi_length", 14)
            )
            for length in (9, 21, 50):
                df[f"EMA_{length}"] = ta.ema(df["close"], length=length)
            atr_len = cfg.get("atr_length", 14)
            df[atr_col(atr_len)] = ta.atr(df["high"], df["low"], df["close"], length=atr_len)
            # 3.4-A: compute ADX for CHART_AGENT trend regime detection
            adx_len = cfg.get("adx_length", 14)
            adx = ta.adx(df["high"], df["low"], df["close"], length=adx_len)
            if adx is not None:
                for col in adx.columns:
                    df[col] = adx[col]

        # Full suite for unknown / backtest-all path
        if strat not in ("MACD_RSI", "BRS_SCALPING", "SUPERTREND_ADX", "VWAP_PULLBACK", "CHART_AGENT"):
            self._compute_all(df, cfg)

    def _compute_all(self, df: pd.DataFrame, cfg: dict) -> None:
        """Compute every indicator (legacy / multi-strategy backtests)."""
        macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
        if macd is not None:
            for col in macd.columns:
                df[col] = macd[col]
        df[rsi_col(14)] = ta.rsi(df["close"], length=14)
        stoch = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3, smooth_k=3)
        if stoch is not None:
            for col in stoch.columns:
                df[col] = stoch[col]
        bb = ta.bbands(df["close"], length=20, std=2)
        if bb is not None:
            for col in bb.columns:
                df[col] = bb[col]
        df[atr_col(14)] = ta.atr(df["high"], df["low"], df["close"], length=14)
        st = ta.supertrend(df["high"], df["low"], df["close"], length=14, multiplier=3.0)
        if st is not None:
            for col in st.columns:
                df[col] = st[col]
        adx = ta.adx(df["high"], df["low"], df["close"], length=14)
        if adx is not None:
            for col in adx.columns:
                df[col] = adx[col]
        self._compute_vwap(df)

    def _compute_vwap(self, df: pd.DataFrame) -> None:
        if "time" not in df.columns or len(df) == 0:
            return
        try:
            df_temp = df.copy()
            first_ts = df_temp["time"].iloc[0]
            unit = "ms" if first_ts > 1e11 else "s"
            df_temp["datetime"] = pd.to_datetime(df_temp["time"], unit=unit)
            df_temp.set_index("datetime", inplace=True)
            df_temp.sort_index(inplace=True)
            vwap = ta.vwap(
                df_temp["high"],
                df_temp["low"],
                df_temp["close"],
                df_temp["volume"],
            )
            if vwap is not None:
                df["VWAP"] = vwap.values
        except Exception as vwap_err:
            self.logger.warning(f"VWAP calculation skipped: {vwap_err}")
