"""Execution cost model for backtests — slippage and fees."""

from __future__ import annotations


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
