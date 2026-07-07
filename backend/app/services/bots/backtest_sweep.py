"""Parameter sweep grid for backtest optimization."""

from __future__ import annotations

import copy
import random
from itertools import product
from typing import Any

MAX_SWEEP_COMBOS = 24
MAX_SWEEP_COMBOS_EXTENDED = 100

SWEEP_PARAM_KEYS = (
    "trailing_stop_percent",
    "take_profit_percent",
    "min_confidence",
    "allocation",
    "slippage_bps",
    "fee_bps",
    "stop_loss_percent",
)

SWEEP_RESERVED_KEYS = frozenset({
    "train_pct", "walk_forward", "max_combos",
    "sweep_objective", "min_trades", "objective",
    "sweep_mode", "sweep_seed",
    "bayesian_patience", "bayesian_startup_trials",
    # Tier 5 performance
    "time_budget_sec", "max_trials",
    # Tier 3 WF validation — never swept as strategy params
    "wf_mode", "purged_splits", "purge_bars", "embargo_pct", "wf_step_pct",
    "final_holdout_pct", "pbo_audit", "pbo_top_k", "pbo_groups",
    "holdout_bars", "holdout_pct",
    # Tier 4 UX / workflow
    "optimize_regime", "portfolio_sweep",
})

SWEEP_MODES = frozenset({"grid", "random", "lhs", "bayesian"})


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


def _build_axes(base: dict, sweep: dict) -> list[tuple[str, list[Any]]]:
    axes: list[tuple[str, list[Any]]] = []
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
    return axes


def _max_combos_for_mode(sweep: dict, sweep_mode: str) -> int:
    from app.services.bots.backtest_trial_budget import resolve_max_trials
    return resolve_max_trials(sweep, sweep_mode)


def _lhs_unit_samples(n_samples: int, n_dims: int, rng: random.Random) -> list[list[float]]:
    if n_dims <= 0:
        return []
    samples = [[0.0] * n_dims for _ in range(n_samples)]
    for dim in range(n_dims):
        perm = list(range(n_samples))
        rng.shuffle(perm)
        for i, bucket in enumerate(perm):
            samples[i][dim] = (bucket + rng.random()) / n_samples
    return samples


def _combo_from_unit_point(base: dict, axes: list[tuple[str, list[Any]]], unit: list[float]) -> dict:
    cfg = copy.deepcopy(base)
    for (key, vals), u in zip(axes, unit):
        idx = min(int(u * len(vals)), len(vals) - 1)
        cfg[key] = vals[idx]
    return cfg


def estimate_sweep_combos(sweep: dict | None) -> dict[str, Any]:
    """Return combo estimates for UI warnings."""
    sweep = sweep or {}
    axes = _build_axes({}, sweep)
    if not axes:
        return {"estimated": 1, "full_grid": 1, "truncated": False, "sweep_mode": "grid"}

    sweep_mode = str(sweep.get("sweep_mode") or "grid").lower()
    if sweep_mode not in SWEEP_MODES:
        sweep_mode = "grid"

    full_grid = 1
    for _, vals in axes:
        full_grid *= len(vals)

    max_combos = _max_combos_for_mode(sweep, sweep_mode)
    if sweep_mode == "bayesian":
        estimated = max_combos
        return {
            "estimated": estimated,
            "full_grid": full_grid,
            "truncated": full_grid > max_combos,
            "max_combos": max_combos,
            "sweep_mode": sweep_mode,
        }

    estimated = min(full_grid, max_combos) if sweep_mode == "grid" else max_combos
    return {
        "estimated": estimated,
        "full_grid": full_grid,
        "truncated": full_grid > max_combos,
        "max_combos": max_combos,
        "sweep_mode": sweep_mode,
    }


def expand_sweep_grid(base_config: dict, sweep: dict | None) -> list[dict]:
    """Build parameter combos via grid, random, Latin hypercube, or bayesian placeholder."""
    base = copy.deepcopy(base_config or {})
    sweep = sweep or {}
    sweep_mode = str(sweep.get("sweep_mode") or "grid").lower()
    if sweep_mode == "bayesian":
        # Trials are generated sequentially by backtest_bayesian.run_bayesian_sweep.
        return []

    axes = _build_axes(base, sweep)

    if not axes:
        return [base]

    sweep_mode = str(sweep.get("sweep_mode") or "grid").lower()
    if sweep_mode not in SWEEP_MODES:
        sweep_mode = "grid"

    seed_raw = sweep.get("sweep_seed")
    seed = int(seed_raw) if seed_raw is not None else None
    rng = random.Random(seed)

    keys = [k for k, _ in axes]
    value_lists = [vals for _, vals in axes]
    full_combos = list(product(*value_lists))
    max_combos = _max_combos_for_mode(sweep, sweep_mode)

    if sweep_mode == "grid":
        combos = full_combos[:max_combos]
    elif sweep_mode == "random":
        if len(full_combos) <= max_combos:
            combos = full_combos
        else:
            combos = rng.sample(full_combos, max_combos)
    else:
        n_samples = min(max_combos, max(len(full_combos), 1))
        unit_samples = _lhs_unit_samples(n_samples, len(axes), rng)
        combos = []
        seen: set[tuple] = set()
        for unit in unit_samples:
            combo_tuple = tuple(
                axes[i][1][min(int(unit[i] * len(axes[i][1])), len(axes[i][1]) - 1)]
                for i in range(len(axes))
            )
            if combo_tuple in seen:
                continue
            seen.add(combo_tuple)
            combos.append(combo_tuple)
        if len(combos) < n_samples and len(full_combos) > len(combos):
            extras = [c for c in full_combos if c not in seen]
            rng.shuffle(extras)
            for extra in extras:
                if len(combos) >= n_samples:
                    break
                combos.append(extra)

    out: list[dict] = []
    for combo in combos:
        cfg = copy.deepcopy(base)
        for key, value in zip(keys, combo):
            cfg[key] = value
        out.append(cfg)
    return out


def count_sweep_axes(sweep: dict | None) -> int:
    """Number of swept parameter axes (excluding reserved keys)."""
    return len(_build_axes({}, sweep or {}))


def count_varying_param_axes(configs: list[dict]) -> int:
    """Count config keys that differ across sweep combos."""
    if len(configs) <= 1:
        return 0
    keys: set[str] = set()
    for cfg in configs:
        keys.update(cfg.keys())
    varying = 0
    for key in sorted(keys):
        if key in SWEEP_RESERVED_KEYS or key in ("sim_mode", "live_parity"):
            continue
        seen = {repr(cfg.get(key)) for cfg in configs}
        if len(seen) > 1:
            varying += 1
    return varying


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


def is_sweep_request(sweep: dict | None, configs: list[dict]) -> bool:
    """True when the request is a multi-config parameter sweep."""
    from app.services.bots.backtest_bayesian import is_bayesian_sweep
    if is_bayesian_sweep(sweep):
        return count_sweep_axes(sweep) > 0
    return len(configs) > 1 or bool(sweep)
