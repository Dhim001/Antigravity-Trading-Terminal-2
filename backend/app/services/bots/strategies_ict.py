"""ICT / Smart Money Concepts strategy.

Detects:
- Order Blocks (OB): last opposing candle before an impulse move
- Fair Value Gaps (FVG): 3-bar gaps where wick of bar[0] doesn't overlap body of bar[2]
- Liquidity Sweeps: price clears a prior swing high/low then reverses

Entry: price retests an OB or FVG zone after a liquidity sweep.
Stop: below/above the OB boundary.
"""

from app.services.bots.indicators import atr_col, merge_strategy_config

from app.services.bots.strategies import BaseStrategy


class IctSmcStrategy(BaseStrategy):
    """ICT Smart Money Concepts — Order Blocks, FVGs, Liquidity Sweeps."""

    def evaluate(self, df_row) -> dict:
        try:
            cfg = merge_strategy_config("ICT_SMC", self.config)
            atr = df_row.get(atr_col(cfg["atr_length"]), 0)
            close = df_row.get("close")
            open_ = df_row.get("open")
            high = df_row.get("high")
            low = df_row.get("low")

            if None in (close, open_, high, low) or atr <= 0:
                return {"signal": "NONE"}

            # ── Order Block detection ──
            # A bullish OB: the last bearish candle before a strong bullish impulse
            # A bearish OB: the last bullish candle before a strong bearish impulse
            ob_lookback = int(cfg.get("ob_lookback", 10))
            bullish_ob = self._detect_bullish_ob(df_row, atr)
            bearish_ob = self._detect_bearish_ob(df_row, atr)

            # ── Fair Value Gap detection ──
            fvg_min = float(cfg.get("fvg_min_gap_pct", 0.0005))
            bullish_fvg = self._detect_bullish_fvg(df_row, fvg_min)
            bearish_fvg = self._detect_bearish_fvg(df_row, fvg_min)

            # ── Liquidity Sweep detection ──
            sweep_lookback = int(cfg.get("sweep_lookback", 20))
            sweep_low = self._detect_sweep_low(df_row, sweep_lookback)
            sweep_high = self._detect_sweep_high(df_row, sweep_lookback)

            # ── Signal logic ──
            # BUY: Liquidity sweep of lows + (bullish OB or bullish FVG)
            # Price must be reclaiming (close > open = bullish candle)
            is_bullish_candle = close > open_
            is_bearish_candle = close < open_

            if is_bullish_candle and sweep_low and (bullish_ob or bullish_fvg):
                sl_distance = 2.0 * atr
                return {
                    "signal": "BUY",
                    "stop_loss_distance": sl_distance,
                    "reasons": self._build_reasons("BUY", bullish_ob, bullish_fvg, sweep_low),
                }

            if is_bearish_candle and sweep_high and (bearish_ob or bearish_fvg):
                sl_distance = 2.0 * atr
                return {
                    "signal": "SELL",
                    "stop_loss_distance": sl_distance,
                    "reasons": self._build_reasons("SELL", bearish_ob, bearish_fvg, sweep_high),
                }

            # ── Exit on structure break ──
            current_side = df_row.get("_current_side", "NONE")
            if current_side == "BUY" and bearish_ob:
                return {"signal": "CLOSE", "stop_loss_distance": 1.5 * atr}
            if current_side == "SELL" and bullish_ob:
                return {"signal": "CLOSE", "stop_loss_distance": 1.5 * atr}

        except Exception:
            pass
        return {"signal": "NONE"}

    # ── Order Block helpers ────────────────────────────────────────

    def _detect_bullish_ob(self, row: dict, atr: float) -> bool:
        """Bullish OB: prior bearish candle followed by a strong bullish impulse.

        We use lookback _prev columns to check if the prior bar was bearish
        and the current bar's range exceeds 1.5x ATR (impulse).
        """
        close = row.get("close", 0)
        open_ = row.get("open", 0)
        prev_close = row.get("close_prev", 0)
        prev_open = row.get("open_prev", 0)

        if not all((close, open_, prev_close, prev_open)):
            return False

        # Prior bar was bearish (down candle)
        prior_bearish = prev_close < prev_open
        # Current bar is a strong bullish impulse
        current_range = close - open_
        impulse = current_range > 1.5 * atr

        return prior_bearish and impulse

    def _detect_bearish_ob(self, row: dict, atr: float) -> bool:
        """Bearish OB: prior bullish candle followed by a strong bearish impulse."""
        close = row.get("close", 0)
        open_ = row.get("open", 0)
        prev_close = row.get("close_prev", 0)
        prev_open = row.get("open_prev", 0)

        if not all((close, open_, prev_close, prev_open)):
            return False

        prior_bullish = prev_close > prev_open
        current_range = open_ - close
        impulse = current_range > 1.5 * atr

        return prior_bullish and impulse

    # ── Fair Value Gap helpers ─────────────────────────────────────

    def _detect_bullish_fvg(self, row: dict, min_gap_pct: float) -> bool:
        """Bullish FVG: gap between bar[-2] high and current bar low.

        Uses prev2_high (bar -2 high) and current low. If current low > prev2_high,
        there's a gap that price may fill = bullish FVG zone below price.
        """
        low = row.get("low", 0)
        prev2_high = row.get("prev2_high")

        if prev2_high is None or prev2_high <= 0 or low <= 0:
            return False

        gap = low - prev2_high
        return gap > prev2_high * min_gap_pct

    def _detect_bearish_fvg(self, row: dict, min_gap_pct: float) -> bool:
        """Bearish FVG: gap between current high and bar[-2] low."""
        high = row.get("high", 0)
        prev2_low = row.get("prev2_low")

        if prev2_low is None or prev2_low <= 0 or high <= 0:
            return False

        gap = prev2_low - high
        return gap > high * min_gap_pct

    # ── Liquidity Sweep helpers ────────────────────────────────────

    def _detect_sweep_low(self, row: dict, lookback: int) -> bool:
        """Sweep low: current low goes below rolling low, then closes back above.

        Uses rolling_low_N column added by prepare_strategy_df.
        """
        low = row.get("low", 0)
        close = row.get("close", 0)
        rolling_low = row.get(f"rolling_low_{lookback}")

        if rolling_low is None or rolling_low <= 0:
            return False

        # Wicked below the rolling low, but closed back above it
        return low < rolling_low and close > rolling_low

    def _detect_sweep_high(self, row: dict, lookback: int) -> bool:
        """Sweep high: current high goes above rolling high, then closes back below."""
        high = row.get("high", 0)
        close = row.get("close", 0)
        rolling_high = row.get(f"rolling_high_{lookback}")

        if rolling_high is None or rolling_high <= 0:
            return False

        return high > rolling_high and close < rolling_high

    # ── Reason builder ─────────────────────────────────────────────

    @staticmethod
    def _build_reasons(side: str, ob: bool, fvg: bool, sweep: bool) -> list[str]:
        reasons = []
        if sweep:
            reasons.append(f"Liquidity sweep {'low' if side == 'BUY' else 'high'}")
        if ob:
            reasons.append(f"{'Bullish' if side == 'BUY' else 'Bearish'} Order Block")
        if fvg:
            reasons.append(f"{'Bullish' if side == 'BUY' else 'Bearish'} Fair Value Gap")
        return reasons
