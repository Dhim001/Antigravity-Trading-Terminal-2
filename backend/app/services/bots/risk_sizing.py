"""Shared entry risk sizing — live bots and backtests use account-as-budget (1% of risk base)."""

from __future__ import annotations

RISK_PCT = 0.01
RISK_BASE_MODES = frozenset({"account_snapshot", "simulated_equity"})
_DEFAULT_RISK_BASE = 10_000.0


def parse_risk_sizing_config(cfg: dict | None) -> dict:
    """
    Parse backtest/live-aligned risk config.

    - account_snapshot: fixed risk_base from config (matches live get_account_balance at deploy/run time)
    - simulated_equity: 1% of running backtest equity at each entry
    """
    config = cfg or {}
    mode = str(config.get("risk_base_mode") or "account_snapshot").lower()
    if mode not in RISK_BASE_MODES:
        mode = "account_snapshot"

    snapshot = config.get("risk_base")
    if snapshot is None:
        snapshot = config.get("account_balance")
    if snapshot is not None:
        try:
            snapshot = max(0.0, float(snapshot))
        except (TypeError, ValueError):
            snapshot = _DEFAULT_RISK_BASE
    else:
        snapshot = _DEFAULT_RISK_BASE

    return {"mode": mode, "snapshot": snapshot}


def enrich_backtest_risk_config(config: dict | None, account_balance: float | None) -> dict:
    """Inject account snapshot from OMS when the client did not send risk_base."""
    cfg = dict(config or {})
    if cfg.get("risk_base") is None and cfg.get("account_balance") is None:
        if account_balance is not None and account_balance > 0:
            cfg["risk_base"] = float(account_balance)
    if not cfg.get("risk_base_mode"):
        cfg["risk_base_mode"] = "account_snapshot"
    return cfg


def entry_risk_amount(risk_cfg: dict, simulated_equity: float) -> float:
    """Dollar risk budget for one entry (1% of risk base)."""
    if risk_cfg.get("mode") == "simulated_equity":
        base = max(float(simulated_equity), 0.0)
    else:
        base = max(float(risk_cfg.get("snapshot") or 0.0), 0.0)
    return base * RISK_PCT


def entry_quantity_from_risk(
    *,
    risk_cfg: dict,
    simulated_equity: float,
    price: float,
    stop_loss: float,
    size_factor: float = 1.0,
    apply_vol_sizing: bool = False,
) -> float:
    """Position size from stop distance and account-as-budget risk."""
    price_diff = abs(price - stop_loss)
    if price_diff <= 0 or price <= 0:
        return 0.0
    risk_amount = entry_risk_amount(risk_cfg, simulated_equity)
    qty = risk_amount / price_diff
    if apply_vol_sizing:
        factor = float(size_factor or 1.0)
        if factor > 0 and factor != 1.0:
            qty *= factor
    return qty
