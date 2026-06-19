"""Parameter sweep grid for backtest optimization."""

from __future__ import annotations

import copy
from itertools import product
from typing import Any

MAX_SWEEP_COMBOS = 24

SWEEP_PARAM_KEYS = (
    "trailing_stop_percent",
    "take_profit_percent",
    "min_confidence",
    "allocation",
    "slippage_bps",
    "fee_bps",
    "stop_loss_percent",
)

SWEEP_RESERVED_KEYS = frozenset({"train_pct", "walk_forward", "max_combos"})


def _coerce_sweep_values(values: list) -> list[Any]:
    cleaned: list[Any] = []
    for v in values:
        if isinstance(v, bool):
            cleaned.append(v)
            continue
        try:
            num = float(v)
            cleaned.append(int(num) if num == int(num) and "." not in str(v) else num)
        except (TypeError, ValueError):
            if v is not None and str(v).strip():
                cleaned.append(v)
    return cleaned


def expand_sweep_grid(base_config: dict, sweep: dict | None) -> list[dict]:
    """Cartesian product of sweep lists merged into base config copies."""
    base = copy.deepcopy(base_config or {})
    axes: list[tuple[str, list[Any]]] = []
    sweep = sweep or {}

    ordered_keys = list(SWEEP_PARAM_KEYS)
    for key in sweep:
        if key not in ordered_keys and key not in SWEEP_RESERVED_KEYS:
            ordered_keys.append(key)

    for key in ordered_keys:
        values = sweep.get(key)
        if not isinstance(values, list) or not values:
            continue
        cleaned = _coerce_sweep_values(values)
        if cleaned:
            axes.append((key, cleaned))

    if not axes:
        return [base]

    keys = [k for k, _ in axes]
    value_lists = [vals for _, vals in axes]
    combos = list(product(*value_lists))
    max_combos = int(sweep.get("max_combos") or MAX_SWEEP_COMBOS)
    max_combos = max(1, min(max_combos, MAX_SWEEP_COMBOS))
    if len(combos) > max_combos:
        combos = combos[:max_combos]

    out: list[dict] = []
    for combo in combos:
        cfg = copy.deepcopy(base)
        for key, value in zip(keys, combo):
            cfg[key] = value
        out.append(cfg)
    return out


def sweep_label(config: dict) -> str:
    parts = []
    for key, val in config.items():
        if key in ("sim_mode",) or val is None:
            continue
        short = (
            key.replace("_percent", "%")
            .replace("trailing_stop", "SL")
            .replace("take_profit", "TP")
            .replace("stop_loss", "SL")
        )
        parts.append(f"{short}={val}")
        if len(parts) >= 4:
            break
    return " · ".join(parts) if parts else "default"
