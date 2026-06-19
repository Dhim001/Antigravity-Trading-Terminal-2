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
)


def expand_sweep_grid(base_config: dict, sweep: dict | None) -> list[dict]:
    """Cartesian product of sweep lists merged into base config copies."""
    base = copy.deepcopy(base_config or {})
    axes: list[tuple[str, list[Any]]] = []

    for key in SWEEP_PARAM_KEYS:
        values = (sweep or {}).get(key)
        if not isinstance(values, list) or not values:
            continue
        cleaned = []
        for v in values:
            try:
                cleaned.append(float(v))
            except (TypeError, ValueError):
                continue
        if cleaned:
            axes.append((key, cleaned))

    if not axes:
        return [base]

    keys = [k for k, _ in axes]
    value_lists = [vals for _, vals in axes]
    combos = list(product(*value_lists))
    if len(combos) > MAX_SWEEP_COMBOS:
        combos = combos[:MAX_SWEEP_COMBOS]

    out: list[dict] = []
    for combo in combos:
        cfg = copy.deepcopy(base)
        for key, value in zip(keys, combo):
            cfg[key] = value
        out.append(cfg)
    return out


def sweep_label(config: dict) -> str:
    parts = []
    for key in SWEEP_PARAM_KEYS:
        if key in config:
            val = config[key]
            short = key.replace("_percent", "%").replace("trailing_stop", "SL").replace("take_profit", "TP")
            parts.append(f"{short}={val}")
    return " · ".join(parts) if parts else "default"
