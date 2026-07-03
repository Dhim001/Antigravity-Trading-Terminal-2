"""Execution cost model for backtests — slippage, fees, and market impact.

Supports two modes:
  1. Flat (default): fixed slippage_bps and fee_bps — matches legacy behaviour.
  2. Volume-participation: slippage scales with order size relative to bar volume,
     modelling realistic market impact for large orders and illiquid assets.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


# ── Legacy flat helpers (unchanged API surface) ──────────────────────────────


def parse_cost_config(config: dict | None) -> tuple[float, float]:
    cfg = config or {}
    slippage_bps = max(0.0, float(cfg.get("slippage_bps") or 0))
    fee_bps = max(0.0, float(cfg.get("fee_bps") or 0))
    return slippage_bps, fee_bps


def entry_fill_price(price: float, side: str, slippage_bps: float) -> float:
    slip = slippage_bps / 10_000.0
    if side == "BUY":
        return price * (1 + slip)
    return price * (1 - slip)


def exit_fill_price(price: float, side: str, slippage_bps: float) -> float:
    """Long exit is a SELL; short exit would be BUY (not used in long-only sim)."""
    slip = slippage_bps / 10_000.0
    if side == "BUY":
        return price * (1 + slip)
    return price * (1 - slip)


def trade_fee(notional: float, fee_bps: float) -> float:
    if fee_bps <= 0 or notional <= 0:
        return 0.0
    return notional * fee_bps / 10_000.0


# ── Advanced cost model ──────────────────────────────────────────────────────


@dataclass
class CostModel:
    """Configurable execution cost model.

    Parameters:
        slippage_bps: base slippage in basis points (flat component)
        fee_bps: exchange/broker fee in basis points
        volume_participation: if True, scale slippage by order/bar volume ratio
        participation_exponent: power-law exponent (0.5 = square-root impact)
        max_participation_pct: max order as % of bar volume (caps fill size)
        min_spread_bps: minimum bid-ask spread floor (added to slippage)
        second_fill_penalty_bps: extra slippage for second fill within same bar
    """

    slippage_bps: float = 0.0
    fee_bps: float = 0.0
    volume_participation: bool = False
    participation_exponent: float = 0.5
    max_participation_pct: float = 10.0  # max 10% of bar volume
    min_spread_bps: float = 0.0
    second_fill_penalty_bps: float = 2.0
    _fills_this_bar: int = field(default=0, repr=False)
    _last_bar_time: Any = field(default=None, repr=False)

    @classmethod
    def from_config(cls, config: dict | None) -> "CostModel":
        """Build from backtest config dict, preserving backward compatibility."""
        cfg = config or {}
        return cls(
            slippage_bps=max(0.0, float(cfg.get("slippage_bps") or 0)),
            fee_bps=max(0.0, float(cfg.get("fee_bps") or 0)),
            volume_participation=bool(cfg.get("volume_participation", False)),
            participation_exponent=float(cfg.get("participation_exponent") or 0.5),
            max_participation_pct=float(cfg.get("max_participation_pct") or 10.0),
            min_spread_bps=max(0.0, float(cfg.get("min_spread_bps") or 0)),
            second_fill_penalty_bps=float(cfg.get("second_fill_penalty_bps") or 2.0),
        )

    def reset_bar(self, bar_time: Any = None) -> None:
        """Call at start of each new bar to reset fill counter."""
        if bar_time != self._last_bar_time:
            self._fills_this_bar = 0
            self._last_bar_time = bar_time

    def effective_slippage_bps(
        self,
        order_notional: float = 0.0,
        bar_volume_notional: float = 0.0,
    ) -> float:
        """Compute effective slippage considering volume participation and spread."""
        base = self.slippage_bps

        # Volume-participation impact: slippage scales with (order/volume)^exponent
        if self.volume_participation and bar_volume_notional > 0 and order_notional > 0:
            participation_ratio = order_notional / bar_volume_notional
            # Square-root impact is the standard market microstructure model
            impact_multiplier = math.pow(
                max(participation_ratio, 1e-9),
                self.participation_exponent,
            )
            # Scale: at 1% participation, impact ≈ base × 0.1^0.5 ≈ base × 0.316
            # at 10% participation, impact ≈ base × 1.0^0.5 ≈ base × 1.0
            # Normalise so 1% participation = 1x base (the "anchor point")
            anchor = math.pow(0.01, self.participation_exponent)
            if anchor > 0:
                base *= max(impact_multiplier / anchor, 0.5)

        # Spread floor
        if self.min_spread_bps > 0:
            half_spread = self.min_spread_bps / 2.0
            base = max(base, half_spread)

        # Second-fill-in-bar penalty (models order book depletion)
        if self._fills_this_bar > 0 and self.second_fill_penalty_bps > 0:
            base += self.second_fill_penalty_bps * self._fills_this_bar

        return base

    def fill_price(
        self,
        price: float,
        side: str,
        *,
        order_notional: float = 0.0,
        bar_volume_notional: float = 0.0,
    ) -> float:
        """Compute fill price with all cost adjustments."""
        slip_bps = self.effective_slippage_bps(order_notional, bar_volume_notional)
        slip = slip_bps / 10_000.0
        self._fills_this_bar += 1
        if side == "BUY":
            return price * (1 + slip)
        return price * (1 - slip)

    def compute_fee(self, notional: float) -> float:
        """Compute exchange/broker fee."""
        if self.fee_bps <= 0 or notional <= 0:
            return 0.0
        return notional * self.fee_bps / 10_000.0

    def max_fill_quantity(
        self,
        price: float,
        bar_volume: float,
    ) -> float | None:
        """Return max order quantity based on volume participation cap, or None if uncapped."""
        if not self.volume_participation or bar_volume <= 0 or self.max_participation_pct <= 0:
            return None
        max_notional = bar_volume * price * (self.max_participation_pct / 100.0)
        if price <= 0:
            return None
        return max_notional / price

    def to_dict(self) -> dict:
        """Serialise for result output."""
        return {
            "slippage_bps": self.slippage_bps,
            "fee_bps": self.fee_bps,
            "volume_participation": self.volume_participation,
            "participation_exponent": self.participation_exponent,
            "max_participation_pct": self.max_participation_pct,
            "min_spread_bps": self.min_spread_bps,
            "second_fill_penalty_bps": self.second_fill_penalty_bps,
        }
