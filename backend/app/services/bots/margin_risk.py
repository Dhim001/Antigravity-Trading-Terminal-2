"""Margin and leverage tracking — utilization caps for bot entries."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import (
    RISK_MARGIN_ENABLED,
    RISK_MAX_LEVERAGE,
    RISK_MAX_MARGIN_UTILIZATION_PCT,
)
from app.services.bots.portfolio_risk import PortfolioSnapshot


@dataclass
class MarginSnapshot:
    enabled: bool
    source: str
    account_equity: float
    available_cash: float
    margin_used: float
    margin_capacity: float
    utilization_pct: float
    max_leverage: float
    buying_power: float | None = None


def margin_status() -> dict:
    return {
        "enabled": RISK_MARGIN_ENABLED,
        "max_utilization_pct": RISK_MAX_MARGIN_UTILIZATION_PCT,
        "max_leverage": RISK_MAX_LEVERAGE,
    }


def _oms_margin_source(oms) -> str:
    name = type(oms).__name__.lower()
    if "etoro" in name:
        return "etoro"
    if "alpaca" in name:
        return "alpaca"
    if "binance" in name:
        return "binance"
    return "sim"


def _cash_from_balances(balances: dict) -> tuple[float, float]:
    for asset in ("USD", "USDT"):
        row = balances.get(asset)
        if row:
            return float(row.get("balance") or 0), float(row.get("locked") or 0)
    return 0.0, 0.0


def build_margin_snapshot(oms, portfolio: PortfolioSnapshot) -> MarginSnapshot:
    account = oms.get_account_data() if oms is not None else {}
    balances = account.get("balances") or {}
    cash, locked = _cash_from_balances(balances)
    source = _oms_margin_source(oms)
    equity = max(float(portfolio.account_equity), 1.0)
    max_leverage = 1.0
    buying_power = None

    margin_block = account.get("margin") or {}
    if margin_block:
        source = str(margin_block.get("source") or source)
        equity = max(float(margin_block.get("equity") or equity), 1.0)
        available = float(margin_block.get("available_cash") or cash)
        margin_used = float(margin_block.get("margin_used") or portfolio.gross_exposure)
        max_leverage = float(margin_block.get("max_leverage") or 1)
        buying_power = margin_block.get("buying_power")
        if buying_power is not None:
            buying_power = float(buying_power)
    else:
        available = max(0.0, cash - locked)
        margin_used = portfolio.gross_exposure
        max_leverage = 1.0

    capacity = max(equity, 1.0)
    utilization = round((margin_used / capacity) * 100, 2) if capacity else 0.0

    return MarginSnapshot(
        enabled=RISK_MARGIN_ENABLED,
        source=source,
        account_equity=round(equity, 2),
        available_cash=round(max(available, 0.0), 2),
        margin_used=round(max(margin_used, 0.0), 2),
        margin_capacity=round(capacity, 2),
        utilization_pct=min(utilization, 999.0),
        max_leverage=max(max_leverage, 1.0),
        buying_power=buying_power,
    )


def entry_margin_required(notional: float, leverage: float = 1.0) -> float:
    lev = max(float(leverage or 1), 1.0)
    return notional / lev


def validate_margin_entry(
    margin: MarginSnapshot | None,
    *,
    price: float,
    quantity: float,
    leverage: float = 1.0,
) -> tuple[bool, str, float | None]:
    if not margin or not margin.enabled:
        return True, "OK", quantity

    lev = max(float(leverage or 1), 1.0)
    if lev > RISK_MAX_LEVERAGE:
        return False, (
            f"Requested leverage {lev:.0f}x exceeds platform max {RISK_MAX_LEVERAGE:.0f}x."
        ), None

    notional = quantity * price
    if notional <= 0:
        return False, "Invalid notional.", None

    required = entry_margin_required(notional, lev)
    min_margin = (price * 0.001) / lev

    if required > margin.available_cash:
        headroom = margin.available_cash
        if headroom < min_margin:
            return False, (
                f"Insufficient margin/cash (${margin.available_cash:,.2f} available)."
            ), None
        capped_qty = (headroom * lev) / price
        return True, f"Capped to available margin (${margin.available_cash:,.0f}).", capped_qty

    max_used = margin.margin_capacity * (RISK_MAX_MARGIN_UTILIZATION_PCT / 100.0)
    projected = margin.margin_used + required
    if projected > max_used:
        headroom = max(0.0, max_used - margin.margin_used)
        if headroom < min_margin:
            return False, (
                f"Margin utilization cap ({RISK_MAX_MARGIN_UTILIZATION_PCT:.0f}% of "
                f"${margin.margin_capacity:,.0f}) reached."
            ), None
        capped_qty = (headroom * lev) / price
        return True, (
            f"Capped to margin limit ({RISK_MAX_MARGIN_UTILIZATION_PCT:.0f}%)."
        ), capped_qty

    return True, "OK", quantity


def margin_to_dict(margin: MarginSnapshot) -> dict:
    return {
        "enabled": margin.enabled,
        "source": margin.source,
        "account_equity": margin.account_equity,
        "available_cash": margin.available_cash,
        "margin_used": margin.margin_used,
        "margin_capacity": margin.margin_capacity,
        "utilization_pct": margin.utilization_pct,
        "max_utilization_pct": RISK_MAX_MARGIN_UTILIZATION_PCT,
        "max_leverage": margin.max_leverage,
        "max_leverage_cap": RISK_MAX_LEVERAGE,
        "buying_power": margin.buying_power,
    }
