"""Risk configuration snapshot and live entry preview for the settings UI."""

from __future__ import annotations

from typing import Any

from app.config import (
    PORTFOLIO_MAX_GROSS_EXPOSURE_PCT,
    PORTFOLIO_MAX_GROUP_EXPOSURE_PCT,
    RISK_KILL_SWITCH_ENABLED,
    RISK_MAX_DRAWDOWN_PCT,
)
from app.services.bots.correlation import summarize_basket_correlation
from app.services.bots.margin_risk import margin_status
from app.services.bots.portfolio_risk import symbol_correlation_group
from app.services.bots.position_duration import position_duration_status
from app.services.bots.risk_gate import RiskGate
from app.services.bots.risk_monitor import compute_drawdown, drawdown_to_dict
from app.services.bots.time_windows import is_no_trade_window, time_controls_status
from app.services.bots.correlation import correlation_status


def _mark_price(oms, symbol: str) -> float:
    sym = (symbol or "").upper()
    feed = getattr(oms, "feed", None)
    if feed and hasattr(feed, "get_market_data"):
        md = feed.get_market_data(sym) or {}
        price = float(md.get("price") or 0)
        if price > 0:
            return price
    account = oms.get_account_data()
    pos = (account.get("positions") or {}).get(sym) or {}
    return float(pos.get("mark") or pos.get("avg_price") or 0)


def get_risk_config(*, oms=None) -> dict[str, Any]:
    drawdown = drawdown_to_dict(compute_drawdown(oms)) if oms else {
        "kill_switch_enabled": RISK_KILL_SWITCH_ENABLED,
        "max_drawdown_pct": RISK_MAX_DRAWDOWN_PCT,
    }
    return {
        "env_readonly": True,
        "kill_switch": {
            "enabled": drawdown.get("kill_switch_enabled", RISK_KILL_SWITCH_ENABLED),
            "max_drawdown_pct": drawdown.get("max_drawdown_pct", RISK_MAX_DRAWDOWN_PCT),
            "tripped": drawdown.get("kill_switch_tripped", False),
            "tripped_at": drawdown.get("kill_switch_tripped_at"),
            "current_drawdown_pct": drawdown.get("current_drawdown_pct"),
        },
        "time_controls": time_controls_status(),
        "position_duration": position_duration_status(),
        "dynamic_correlation": correlation_status(),
        "portfolio_limits": {
            "max_gross_exposure_pct": PORTFOLIO_MAX_GROSS_EXPOSURE_PCT,
            "max_group_exposure_pct": PORTFOLIO_MAX_GROUP_EXPOSURE_PCT,
        },
        "margin": margin_status(),
    }


def preview_entry(
    oms,
    *,
    symbol: str,
    side: str,
    notional: float | None = None,
    quantity: float | None = None,
    price: float | None = None,
    risk_gate: RiskGate | None = None,
) -> dict[str, Any]:
    sym = (symbol or "").strip().upper()
    side_u = (side or "BUY").strip().upper()
    if side_u not in ("BUY", "SELL"):
        side_u = "BUY"

    mark = float(price or 0)
    if mark <= 0:
        mark = _mark_price(oms, sym)
    if mark <= 0:
        return {"error": f"No price available for {sym}"}

    qty = float(quantity or 0)
    if qty <= 0:
        notional_f = float(notional or 0)
        if notional_f <= 0:
            return {"error": "Provide notional or quantity"}
        qty = notional_f / mark

    gate = risk_gate or RiskGate()
    checks: list[dict[str, Any]] = []

    drawdown = drawdown_to_dict(compute_drawdown(oms))
    if drawdown.get("kill_switch_tripped"):
        checks.append({
            "id": "kill_switch",
            "allowed": False,
            "message": "Drawdown kill switch is tripped — new entries blocked.",
        })
    elif not drawdown.get("kill_switch_enabled", True):
        checks.append({
            "id": "kill_switch",
            "allowed": True,
            "message": "Kill switch disabled.",
        })
    else:
        checks.append({
            "id": "kill_switch",
            "allowed": True,
            "message": f"Drawdown {drawdown.get('current_drawdown_pct', 0):.1f}% / {drawdown.get('max_drawdown_pct', 0):.1f}% limit.",
        })

    in_window, window_reason = is_no_trade_window(None, sym)
    checks.append({
        "id": "no_trade_window",
        "allowed": not in_window,
        "message": window_reason if in_window else "Outside equity no-trade windows.",
    })

    group = symbol_correlation_group(sym)
    checks.append({
        "id": "correlation_group",
        "allowed": True,
        "message": f"Symbol maps to correlation group '{group}'.",
        "group": group,
    })

    port_decision = gate.validate_portfolio(
        oms,
        sym,
        side_u,
        qty,
        mark,
        is_exit=False,
    )
    checks.append({
        "id": "portfolio_limits",
        "allowed": port_decision.allowed,
        "message": port_decision.reason,
        "capped_quantity": port_decision.quantity,
    })

    blocked = [c for c in checks if not c.get("allowed")]
    allowed = not blocked
    return {
        "symbol": sym,
        "side": side_u,
        "price": round(mark, 4),
        "quantity": round(qty, 6),
        "notional": round(qty * mark, 2),
        "allowed": allowed,
        "checks": checks,
        "block_reason": blocked[0]["message"] if blocked else None,
    }
