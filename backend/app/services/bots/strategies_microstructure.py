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


def _hist_slice(history: deque, start: int | None = None, end: int | None = None) -> list:
    """Slice a deque safely (deque itself does not support slicing)."""
    items = list(history)
    if start is None and end is None:
        return items
    return items[start:end]


class CvdDivergenceStrategy(BaseStrategy):
    """Detects when price and CVD diverge (hidden buying/selling)."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.history = deque(maxlen=20)
        
    def evaluate(self, df_row) -> dict:
        self.history.append(df_row)
            
        try:
            cfg = merge_strategy_config("CVD_DIVERGENCE", self.config)
            atr = float(df_row.get(atr_col(cfg["atr_length"])) or 0)
            adx = float(df_row.get(adx_col(cfg["adx_length"])) or 0)
            cvd_raw = df_row.get("cvd")
            if atr <= 0 or cvd_raw is None:
                return {"signal": "NONE", "reject_reason": "ATR/CVD unavailable"}
            cvd = float(cvd_raw)

            # Require non-trending state (divergence works best in range to trend transition)
            adx_thresh = float(cfg.get("adx_threshold", 40))
            if adx and adx > adx_thresh:
                return {"signal": "NONE", "reject_reason": "ADX trend filter"}

            if len(self.history) < 10:
                return {"signal": "NONE", "reject_reason": "warming up"}
                
            # Find recent pivots in our short history window (10 bars)
            window = _hist_slice(self.history, -10, None)
            prices = [r.get("close", 0) for r in window]
            cvds = [r.get("cvd", 0) for r in window]
            
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
            return {"signal": "NONE", "reject_reason": "no CVD divergence"}

        except Exception as exc:
            return {"signal": "NONE", "reject_reason": f"evaluate error: {exc}"}


class WyckoffStrategy(BaseStrategy):
    """Detects Wyckoff accumulation 'Springs' and distribution 'Upthrusts'."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.history = deque(maxlen=30)
        
    def evaluate(self, df_row) -> dict:
        self.history.append(df_row)
            
        try:
            cfg = merge_strategy_config("WYCKOFF_SPRING", self.config)
            atr = float(df_row.get(atr_col(cfg["atr_length"])) or 0)
            if atr <= 0 or len(self.history) < 20:
                return {"signal": "NONE", "reject_reason": "ATR/warmup"}

            # Build trading range from older history (exclude last 3 bars)
            range_history = _hist_slice(self.history, -30, -3)
            if len(range_history) < 5:
                return {"signal": "NONE", "reject_reason": "insufficient range history"}
            highs = [r.get("high", 0) for r in range_history]
            lows = [r.get("low", 0) for r in range_history]
            
            range_high = max(highs)
            range_low = min(lows)
            
            # We need the range to be somewhat tight, indicating consolidation
            if range_high - range_low > atr * 5:
                return {"signal": "NONE", "reject_reason": "range too wide"}

            hist = list(self.history)
            prev_bar = hist[-2]
            curr_bar = hist[-1]
            
            p_low, p_high, p_close, p_vol = prev_bar.get("low", 0), prev_bar.get("high", 0), prev_bar.get("close", 0), prev_bar.get("volume", 0)
            c_close = curr_bar.get("close", 0)
            
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
            return {"signal": "NONE", "reject_reason": "no spring/upthrust"}

        except Exception as exc:
            return {"signal": "NONE", "reject_reason": f"evaluate error: {exc}"}


class VpocReversionStrategy(BaseStrategy):
    """Mean reversion towards Volume Profile Point of Control."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.lookback = int(
            merge_strategy_config("VPOC_REVERSION", self.config).get("profile_lookback", 100)
        )
        self.history = deque(maxlen=self.lookback)
        
    def evaluate(self, df_row) -> dict:
        self.history.append(df_row)
            
        try:
            cfg = merge_strategy_config("VPOC_REVERSION", self.config)
            atr = float(df_row.get(atr_col(cfg["atr_length"])) or 0)
            adx = float(df_row.get(adx_col(cfg["adx_length"])) or 0)
            rsi = float(df_row.get(rsi_col(cfg["rsi_length"])) or 50)
            close = float(df_row.get("close") or 0)
            
            if atr <= 0 or len(self.history) < self.lookback // 2:
                return {"signal": "NONE", "reject_reason": "ATR/warmup"}

            # Do not trade mean reversion in strong trends
            adx_filter = float(cfg.get("adx_trend_filter", 35))
            if adx and adx > adx_filter:
                return {"signal": "NONE", "reject_reason": "ADX trend filter"}

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
                return {"signal": "NONE", "reject_reason": "empty volume profile"}
                
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
            return {"signal": "NONE", "reject_reason": "inside value area / RSI filter"}

        except Exception as exc:
            return {"signal": "NONE", "reject_reason": f"evaluate error: {exc}"}


def _level_size(level) -> float:
    """Extract size from [price, size] or {price, size/qty/quantity}."""
    if level is None:
        return 0.0
    if isinstance(level, (list, tuple)) and len(level) >= 2:
        try:
            return float(level[1] or 0)
        except (TypeError, ValueError):
            return 0.0
    if isinstance(level, dict):
        for key in ("size", "qty", "quantity", "volume", "sz"):
            if level.get(key) is not None:
                try:
                    return float(level[key] or 0)
                except (TypeError, ValueError):
                    return 0.0
    return 0.0


def compute_bair_mlofi(orderbook: dict | None, *, levels: int = 5) -> tuple[float | None, float | None]:
    """Bid-Ask Imbalance Ratio + Multi-Level Order Flow Imbalance from L2."""
    if not isinstance(orderbook, dict):
        return None, None
    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []
    if not bids or not asks:
        return None, None

    n = max(1, min(int(levels or 5), 20))
    bid0 = _level_size(bids[0])
    ask0 = _level_size(asks[0])
    denom0 = bid0 + ask0
    bair = ((bid0 - ask0) / denom0) if denom0 > 0 else None

    num = 0.0
    den = 0.0
    for i in range(n):
        b = _level_size(bids[i]) if i < len(bids) else 0.0
        a = _level_size(asks[i]) if i < len(asks) else 0.0
        w = 1.0 / float(i + 1)
        num += (b - a) * w
        den += (b + a) * w
    mlofi = (num / den) if den > 0 else None
    return bair, mlofi


class OrderFlowImbalanceStrategy(BaseStrategy):
    """Detects bid/ask imbalances from orderbook snapshots (with OHLCV proxy)."""

    def evaluate(self, df_row) -> dict:
        try:
            cfg = merge_strategy_config("ORDERFLOW_IMBALANCE", self.config)
            atr = float(df_row.get(atr_col(cfg["atr_length"])) or 0)
            if atr <= 0:
                return {"signal": "NONE", "reject_reason": "ATR unavailable"}

            rsi_len = int(cfg.get("rsi_length", 14))
            rsi = df_row.get(rsi_col(rsi_len))
            if rsi is None:
                rsi = df_row.get("RSI_14")
            try:
                rsi = float(rsi) if rsi is not None else 50.0
            except (TypeError, ValueError):
                rsi = 50.0

            vol = float(df_row.get("volume") or 0)
            vol_ma_len = int(cfg.get("volume_ma_length", 20))
            vol_ma = float(df_row.get(f"volume_ma_{vol_ma_len}") or 0)
            surge_mult = float(cfg.get("volume_surge_mult", 1.3))
            if vol_ma <= 0 or vol < vol_ma * surge_mult:
                return {
                    "signal": "NONE",
                    "reject_reason": "volume surge not met",
                    "volume": vol,
                    "volume_ma": vol_ma,
                }

            bair_th = float(cfg.get("bair_threshold", 0.3))
            mlofi_th = float(cfg.get("mlofi_threshold", 0.2))
            rsi_ob = float(cfg.get("rsi_overbought", 65))
            rsi_os = float(cfg.get("rsi_oversold", 35))
            levels = int(cfg.get("book_levels", 5))
            allow_proxy = bool(cfg.get("allow_candle_proxy", True))

            book = df_row.get("_orderbook")
            bair, mlofi = compute_bair_mlofi(book, levels=levels)
            source = "orderbook"

            # Fall back to candle proxy when book missing or nearly balanced (synthetic).
            book_weak = (
                bair is None
                or mlofi is None
                or (abs(float(bair)) < bair_th * 0.5 and abs(float(mlofi)) < mlofi_th * 0.5)
            )
            if book_weak and allow_proxy:
                try:
                    pb = float(df_row.get("ofi_bair_proxy"))
                    pm = float(df_row.get("ofi_mlofi_proxy"))
                    if math.isfinite(pb) and math.isfinite(pm):
                        bair, mlofi = pb, pm
                        source = "candle_proxy"
                except (TypeError, ValueError):
                    pass

            if bair is None or mlofi is None:
                return {
                    "signal": "NONE",
                    "reject_reason": "no orderbook and no candle proxy",
                }

            reasons: list[str] = []
            signal = "NONE"

            if bair > bair_th and mlofi > mlofi_th and rsi < rsi_ob:
                signal = "BUY"
                reasons.append(
                    f"Bid pressure ({source}): BAIR={bair:.2f} MLOFI={mlofi:.2f} "
                    f"vol={vol:.0f}/{vol_ma:.0f}× RSI={rsi:.1f}"
                )
            elif bair < -bair_th and mlofi < -mlofi_th and rsi > rsi_os:
                signal = "SELL"
                reasons.append(
                    f"Ask pressure ({source}): BAIR={bair:.2f} MLOFI={mlofi:.2f} "
                    f"vol={vol:.0f}/{vol_ma:.0f}× RSI={rsi:.1f}"
                )

            if signal == "NONE":
                return {
                    "signal": "NONE",
                    "reject_reason": "imbalance / RSI filters",
                    "bair": round(float(bair), 4),
                    "mlofi": round(float(mlofi), 4),
                    "ofi_source": source,
                    "rsi": rsi,
                }

            return {
                "signal": signal,
                "stop_loss_distance": 1.5 * atr,
                "reasons": reasons,
                "bair": round(float(bair), 4),
                "mlofi": round(float(mlofi), 4),
                "ofi_source": source,
                "confidence": min(1.0, (abs(float(bair)) + abs(float(mlofi))) / 2.0),
            }
        except Exception:
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
