"""Spread-capture Market Making strategy (Hummingbot-style).

Designed for crypto pairs with the bar-close engine.
Uses bid/ask proxies derived from OHLC data and manages inventory skew.

Configurable:
  spread_pct: 0.002        minimum spread to capture (0.2%)
  inventory_target: 0.0    neutral inventory (0 = flat)
  max_skew: 0.5            max inventory imbalance before one-sided quoting
  atr_length: 14
  vol_shutdown_mult: 2.5   shut down if ATR > this * median (too volatile)
"""

from app.services.bots.indicators import atr_col, merge_strategy_config

from app.services.bots.strategies import BaseStrategy


class MarketMakingStrategy(BaseStrategy):
    """Simple spread-capture market making with inventory management."""

    def evaluate(self, df_row) -> dict:
        try:
            cfg = merge_strategy_config("MARKET_MAKING", self.config)
            spread_pct = float(cfg.get("spread_pct", 0.002))
            inventory_target = float(cfg.get("inventory_target", 0.0))
            max_skew = float(cfg.get("max_skew", 0.5))
            atr_len = int(cfg.get("atr_length", 14))
            vol_mult = float(cfg.get("vol_shutdown_mult", 2.5))

            close = df_row.get("close")
            high = df_row.get("high")
            low = df_row.get("low")
            atr = df_row.get(atr_col(atr_len), 0)

            if None in (close, high, low) or close <= 0 or atr <= 0:
                return {"signal": "NONE"}

            # ── Volatility shutdown ──
            # In extremely volatile conditions, market making is unprofitable
            atr_median = df_row.get(f"ATR_{atr_len}_median_20")
            if atr_median and atr_median > 0 and vol_mult > 0:
                if atr > atr_median * vol_mult:
                    return {"signal": "NONE"}

            # ── Spread assessment ──
            # Use high-low as a proxy for the bid-ask spread
            bar_spread = (high - low) / close if close > 0 else 0
            if bar_spread < spread_pct:
                # Spread too tight to capture profitably
                return {"signal": "NONE"}

            mid = (high + low) / 2.0
            bid_zone = mid - (mid * spread_pct / 2)
            ask_zone = mid + (mid * spread_pct / 2)

            # ── Inventory management ──
            current_side = df_row.get("_current_side", "NONE")

            # Determine inventory skew
            # Positive skew = we're long, need to sell
            # Negative skew = we're short, need to buy
            if current_side == "BUY":
                inventory_skew = 1.0 - inventory_target
            elif current_side == "SELL":
                inventory_skew = -1.0 - inventory_target
            else:
                inventory_skew = 0.0 - inventory_target

            # ── Signal logic ──
            # If we're too long (skew > max), only issue SELL signals
            if inventory_skew > max_skew:
                if close >= ask_zone:
                    return {
                        "signal": "CLOSE",
                        "stop_loss_distance": 1.5 * atr,
                        "reasons": ["Inventory rebalance — closing long at ask zone"],
                    }
                return {"signal": "NONE"}

            # If we're too short (skew < -max), only issue BUY signals
            if inventory_skew < -max_skew:
                if close <= bid_zone:
                    return {
                        "signal": "CLOSE",
                        "stop_loss_distance": 1.5 * atr,
                        "reasons": ["Inventory rebalance — closing short at bid zone"],
                    }
                return {"signal": "NONE"}

            # Normal market making: BUY at bid zone, SELL at ask zone
            if close <= bid_zone and current_side != "BUY":
                return {
                    "signal": "BUY",
                    "stop_loss_distance": 1.5 * atr,
                    "take_profit_price": ask_zone,
                    "reasons": [
                        f"MM bid zone entry (spread {bar_spread:.4%})",
                        f"Target ask zone {ask_zone:.4f}",
                    ],
                }

            if close >= ask_zone and current_side != "SELL":
                return {
                    "signal": "SELL",
                    "stop_loss_distance": 1.5 * atr,
                    "take_profit_price": bid_zone,
                    "reasons": [
                        f"MM ask zone entry (spread {bar_spread:.4%})",
                        f"Target bid zone {bid_zone:.4f}",
                    ],
                }

        except Exception:
            pass
        return {"signal": "NONE"}
