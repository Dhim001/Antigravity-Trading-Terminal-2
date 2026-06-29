"""Donchian Channel Breakout / Momentum strategy.

Classic systematic trend-following:
- Entry: price breaks the N-period high/low (Donchian Channel)
- ATR expansion filter: only enter when current ATR > median ATR (avoids false choppy breakouts)
- Exit: price breaks the shorter exit-channel in the opposite direction
- Optional trailing stop via Chandelier ATR

Configurable:
  breakout_length: 20  (entry channel lookback)
  exit_length: 10       (exit channel lookback)
  atr_confirm_mult: 1.0 (ATR must be > this * ATR median to confirm)
  atr_length: 14
"""

from app.services.bots.indicators import atr_col, merge_strategy_config

from app.services.bots.strategies import BaseStrategy


class DonchianBreakoutStrategy(BaseStrategy):
    """Donchian Channel breakout with ATR expansion confirmation."""

    def evaluate(self, df_row) -> dict:
        try:
            cfg = merge_strategy_config("DONCHIAN_BREAKOUT", self.config)
            breakout_len = int(cfg.get("breakout_length", 20))
            exit_len = int(cfg.get("exit_length", 10))
            atr_mult = float(cfg.get("atr_confirm_mult", 1.0))
            atr_len = int(cfg.get("atr_length", 14))

            close = df_row.get("close")
            high = df_row.get("high")
            low = df_row.get("low")
            atr = df_row.get(atr_col(atr_len), 0)

            if None in (close, high, low) or atr <= 0:
                return {"signal": "NONE"}

            # Donchian channels from prepare_strategy_df columns
            dc_high = df_row.get(f"dc_high_{breakout_len}")
            dc_low = df_row.get(f"dc_low_{breakout_len}")
            dc_exit_high = df_row.get(f"dc_high_{exit_len}")
            dc_exit_low = df_row.get(f"dc_low_{exit_len}")

            if dc_high is None or dc_low is None:
                return {"signal": "NONE"}

            # ATR expansion filter: current ATR vs rolling median
            atr_median = df_row.get(f"ATR_{atr_len}_median_20")
            atr_expanding = True
            if atr_median and atr_median > 0 and atr_mult > 0:
                atr_expanding = atr >= (atr_median * atr_mult)

            current_side = df_row.get("_current_side", "NONE")

            # ── Exit signals (shorter channel) ──
            if current_side == "BUY" and dc_exit_low is not None and close <= dc_exit_low:
                return {
                    "signal": "CLOSE",
                    "stop_loss_distance": 1.5 * atr,
                    "reasons": [f"Exit channel low break ({exit_len}-bar)"],
                }

            if current_side == "SELL" and dc_exit_high is not None and close >= dc_exit_high:
                return {
                    "signal": "CLOSE",
                    "stop_loss_distance": 1.5 * atr,
                    "reasons": [f"Exit channel high break ({exit_len}-bar)"],
                }

            # ── Entry signals (full channel + ATR confirmation) ──
            if high >= dc_high and atr_expanding:
                return {
                    "signal": "BUY",
                    "stop_loss_distance": 2.0 * atr,
                    "reasons": [
                        f"Donchian {breakout_len}-bar high breakout",
                        "ATR expansion confirmed" if atr_median else "ATR filter N/A",
                    ],
                }

            if low <= dc_low and atr_expanding:
                return {
                    "signal": "SELL",
                    "stop_loss_distance": 2.0 * atr,
                    "reasons": [
                        f"Donchian {breakout_len}-bar low breakout",
                        "ATR expansion confirmed" if atr_median else "ATR filter N/A",
                    ],
                }

        except Exception:
            pass
        return {"signal": "NONE"}
