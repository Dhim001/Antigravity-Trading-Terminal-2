"""Per-broker order UX capabilities exposed to the frontend."""

from __future__ import annotations

from app.config import TERMINAL_MODE

# Wire shape documented in docs/ORDER_EXECUTION.md
_DEFAULT = {
    "partial_close": True,
    "reverse_position": True,
    "bracket_orders": False,
    "oco": False,
    "trailing_stop_manual": False,
    "order_preview_costs": True,
}

_BY_MODE: dict[str, dict] = {
    "SIMULATED": {
        **_DEFAULT,
        "partial_close": True,
        "reverse_position": True,
        "bracket_orders": True,
        "oco": True,
        "trailing_stop_manual": True,
    },
    "LIVE_MASSIVE": {
        **_DEFAULT,
        "partial_close": True,
        "reverse_position": True,
        "bracket_orders": True,
        "oco": True,
        "trailing_stop_manual": True,
    },
    "LIVE_ETORO": {
        **_DEFAULT,
        "partial_close": True,
        "reverse_position": False,
        "bracket_orders": True,
        "oco": False,
    },
    "LIVE_ALPACA": {
        **_DEFAULT,
        "partial_close": True,
        "reverse_position": True,
        "bracket_orders": True,
        "oco": True,
    },
    "LIVE_BINANCE": {
        **_DEFAULT,
        "partial_close": True,
        "reverse_position": True,
    },
    "LIVE_IB": {
        **_DEFAULT,
        "partial_close": True,
        "reverse_position": False,
        "bracket_orders": False,
    },
}


def get_order_capabilities(oms=None) -> dict:
    """Return capability flags for the active terminal / OMS."""
    mode = TERMINAL_MODE
    caps = dict(_BY_MODE.get(mode, _DEFAULT))
    caps["broker"] = mode

    oms_name = type(oms).__name__.lower() if oms is not None else ""
    if oms is not None and getattr(oms, "use_fallback", False):
        caps["broker"] = f"{mode}:fallback"

    if "sim" in oms_name and mode not in ("SIMULATED", "LIVE_MASSIVE"):
        caps["partial_close"] = True
        caps["reverse_position"] = True

    return caps
