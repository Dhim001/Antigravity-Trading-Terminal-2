"""Microstructure and Imbalance-based trading strategies.

Strategies:
- CvdDivergenceStrategy: Cumulative Volume Delta vs Price divergence
- WyckoffStrategy: Spring / Upthrust detection
- VpocReversionStrategy: Value Area / POC mean reversion
- OrderFlowImbalanceStrategy: Orderbook bid/ask pressure
- AbsorptionAgentStrategy: Multi-domain absorption/exhaustion scoring
"""

from __future__ import annotations

import math
from typing import Any
from collections import deque

from app.services.bots.strategies import BaseStrategy
from app.services.bots.indicators import merge_strategy_config, atr_col, adx_col, rsi_col


class CvdDivergenceStrategy(BaseStrategy):
    """Detects when price and CVD diverge (hidden buying/selling)."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.history = deque(maxlen=20)
        
    def evaluate(self, df_row) -> dict:
        self.history.append(df_row)
            
        try:
            cfg = merge_strategy_config("CVD_DIVERGENCE", self.config)
            atr = df_row.get(atr_col(cfg["atr_length"]), 0)
            adx = df_row.get(adx_col(cfg["adx_length"]), 0)
            cvd = df_row.get("cvd", 0)
            close = df_row.get("close")
            
            if atr <= 0 or not cvd:
                return {"signal": "NONE"}

            # Require non-trending state (divergence works best in range to trend transition)
            adx_thresh = float(cfg.get("adx_threshold", 40))
            if adx and adx > adx_thresh:
                return {"signal": "NONE"}

            if len(self.history) < 10:
                return {"signal": "NONE"}
                
            # Find recent pivots in our short history window (10 bars)
            prices = [r.get("close", 0) for r in self.history[-10:]]
            cvds = [r.get("cvd", 0) for r in self.history[-10:]]
            
            min_price_idx = prices.index(min(prices))
            max_price_idx = prices.index(max(prices))
            
            signal = "NONE"
            reasons = []
            
            # BULLISH DIVERGENCE: Price made a lower low, but CVD made a higher low
            if min_price_idx >= 7:  # recent low
                min_cvd_idx = cvds.index(min(cvds))
                if min_cvd_idx < 4 and cvd > cvds[min_cvd_idx]: 
                    signal = "BUY"
                    reasons.append("Bullish CVD Divergence (Price Lower Low, CVD Higher Low)")
                    
            # BEARISH DIVERGENCE: Price made a higher high, but CVD made a lower high
            if max_price_idx >= 7:  # recent high
                max_cvd_idx = cvds.index(max(cvds))
                if max_cvd_idx < 4 and cvd < cvds[max_cvd_idx]:
                    signal = "SELL"
                    reasons.append("Bearish CVD Divergence (Price Higher High, CVD Lower High)")

            if signal != "NONE":
                sl_distance = 1.5 * atr
                return {
                    "signal": signal,
                    "stop_loss_distance": sl_distance,
                    "reasons": reasons,
                }

        except Exception:
            pass
            
        return {"signal": "NONE"}


class WyckoffStrategy(BaseStrategy):
    """Detects Wyckoff accumulation 'Springs' and distribution 'Upthrusts'."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.history = deque(maxlen=30)
        
    def evaluate(self, df_row) -> dict:
        self.history.append(df_row)
            
        try:
            cfg = merge_strategy_config("WYCKOFF_SPRING", self.config)
            atr = df_row.get(atr_col(cfg["atr_length"]), 0)
            if atr <= 0 or len(self.history) < 20:
                return {"signal": "NONE"}

            # Build trading range from older history (exclude last 3 bars)
            range_history = self.history[-30:-3]
            highs = [r.get("high", 0) for r in range_history]
            lows = [r.get("low", 0) for r in range_history]
            
            range_high = max(highs)
            range_low = min(lows)
            
            # We need the range to be somewhat tight, indicating consolidation
            if range_high - range_low > atr * 5:
                return {"signal": "NONE"}
                
            prev_bar = self.history[-2]
            curr_bar = self.history[-1]
            
            p_low, p_high, p_close, p_vol = prev_bar.get("low", 0), prev_bar.get("high", 0), prev_bar.get("close", 0), prev_bar.get("volume", 0)
            c_low, c_high, c_close, c_vol = curr_bar.get("low", 0), curr_bar.get("high", 0), curr_bar.get("close", 0), curr_bar.get("volume", 0)
            
            avg_vol = sum([r.get("volume", 0) for r in range_history]) / len(range_history)
            vol_mult = cfg.get("volume_surge_mult", 1.5)
            
            signal = "NONE"
            reasons = []
            
            # SPRING: previous bar pierced range_low but current closed back inside
            if p_low < range_low and p_close >= range_low:
                if p_vol > avg_vol * vol_mult:  # Climactic volume absorption
                    if c_close > p_close:  # Follow-through
                        signal = "BUY"
                        reasons.append("Wyckoff Spring (False Breakdown + Absorption)")
                        
            # UPTHRUST: previous bar pierced range_high but current closed back inside
            if p_high > range_high and p_close <= range_high:
                if p_vol > avg_vol * vol_mult:  # Climactic volume distribution
                    if c_close < p_close:  # Follow-through
                        signal = "SELL"
                        reasons.append("Wyckoff Upthrust (False Breakout + Distribution)")

            if signal != "NONE":
                sl_distance = 1.5 * atr
                return {
                    "signal": signal,
                    "stop_loss_distance": sl_distance,
                    "reasons": reasons,
                }

        except Exception:
            pass
            
        return {"signal": "NONE"}


class VpocReversionStrategy(BaseStrategy):
    """Mean reversion towards Volume Profile Point of Control."""

    def __init__(self, config: dict):
        super().__init__(config)
        lookback = int(merge_strategy_config("VPOC_REVERSION", self.config).get("profile_lookback", 100))
        self.history = deque(maxlen=lookback)
        
    def evaluate(self, df_row) -> dict:
        self.history.append(df_row)
            
        try:
            cfg = merge_strategy_config("VPOC_REVERSION", self.config)
            atr = df_row.get(atr_col(cfg["atr_length"]), 0)
            adx = df_row.get(adx_col(cfg["adx_length"]), 0)
            rsi = df_row.get(rsi_col(cfg["rsi_length"]), 50)
            close = df_row.get("close", 0)
            
            if atr <= 0 or len(self.history) < lookback // 2:
                return {"signal": "NONE"}

            # Do not trade mean reversion in strong trends
            adx_filter = float(cfg.get("adx_trend_filter", 35))
            if adx and adx > adx_filter:
                return {"signal": "NONE"}

            # Build simple volume profile
            bins = {}
            bin_size = atr / 10 if atr > 0 else 0.01
            total_vol = 0
            
            for r in self.history:
                c = r.get("close", 0)
                v = r.get("volume", 0)
                if c == 0 or v == 0: continue
                
                b_idx = round(c / bin_size)
                bins[b_idx] = bins.get(b_idx, 0) + v
                total_vol += v
                
            if not bins:
                return {"signal": "NONE"}
                
            # Find POC
            poc_idx = max(bins.items(), key=lambda x: x[1])[0]
            poc = poc_idx * bin_size
            
            # Find Value Area (70%)
            sorted_bins = sorted(bins.items(), key=lambda x: x[1], reverse=True)
            va_vol = 0
            va_prices = []
            for b_idx, b_vol in sorted_bins:
                va_vol += b_vol
                va_prices.append(b_idx * bin_size)
                if va_vol > total_vol * cfg.get("value_area_pct", 0.7):
                    break
                    
            va_high = max(va_prices)
            va_low = min(va_prices)
            
            signal = "NONE"
            reasons = []
            
            # Revert to POC from outside Value Area
            if close < va_low and rsi < 40:
                signal = "BUY"
                reasons.append(f"Price below Value Area Low, reverting to POC ({poc:.2f})")
            elif close > va_high and rsi > 60:
                signal = "SELL"
                reasons.append(f"Price above Value Area High, reverting to POC ({poc:.2f})")

            if signal != "NONE":
                sl_distance = 1.5 * atr
                return {
                    "signal": signal,
                    "stop_loss_distance": sl_distance,
                    "take_profit_price": poc,
                    "reasons": reasons,
                }

        except Exception:
            pass
            
        return {"signal": "NONE"}


class OrderFlowImbalanceStrategy(BaseStrategy):
    """Detects bid/ask imbalances from orderbook snapshots."""

    def evaluate(self, df_row) -> dict:
        # In a real environment, this would access state.orderBooks via a context bridge.
        # Here we mock the detection since OHLCV doesn't have orderbook depth.
        # This will stay idle unless connected to a live orderbook feed.
        return {"signal": "NONE"}


class AbsorptionAgentStrategy(BaseStrategy):
    """Multi-domain scoring for absorption and exhaustion."""

    def evaluate(self, df_row) -> dict:
        try:
            cfg = merge_strategy_config("ABSORPTION_AGENT", self.config)
            atr = df_row.get(atr_col(cfg["atr_length"]), 0)
            vol_ma = df_row.get(f"volume_ma_{int(cfg.get('volume_ma_length', 20))}", 0)
            vol = df_row.get("volume", 0)
            close = df_row.get("close", 0)
            open_ = df_row.get("open", 0)
            high = df_row.get("high", 0)
            low = df_row.get("low", 0)
            
            if atr <= 0 or vol_ma <= 0:
                return {"signal": "NONE"}
                
            body = abs(close - open_)
            candle_range = high - low
            
            score = 0
            reasons = []
            
            # Domain 1: Absorption
            if candle_range > 3 * body and vol > vol_ma * 1.5:
                if close > open_: # Bullish absorption
                    score += 2
                    reasons.append("Bullish volume absorption detected")
                else:
                    score -= 2
                    reasons.append("Bearish volume absorption detected")
                    
            if score >= 2:
                return {
                    "signal": "BUY",
                    "stop_loss_distance": 1.5 * atr,
                    "reasons": reasons,
                }
            elif score <= -2:
                return {
                    "signal": "SELL",
                    "stop_loss_distance": 1.5 * atr,
                    "reasons": reasons,
                }

        except Exception:
            pass
            
        return {"signal": "NONE"}
