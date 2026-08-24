"""Armed chandelier / profit-floor ratchet shared by live SL checks and backtests.

``arm_r <= 0`` keeps the legacy immediate trail (RL). ``arm_r > 0`` holds the
initial hard stop until favorable excursion reaches that many R, then floors
at breakeven and trails the high/low by N×ATR so a 2% trail cannot donate a
2.2% run.
"""

from __future__ import annotations

from typing import Any


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def resolve_r_distance(
    *,
    avg_price: float,
    stop_loss_percent: float | None,
    entry_atr: float | None,
    chandelier_multiplier: float,
) -> float:
    """1R in price units. Prefer the configured percent stop (stable after ratchets)."""
    avg = _as_float(avg_price)
    if avg <= 0:
        return 0.0
    pct = _as_float(stop_loss_percent) if stop_loss_percent is not None else 0.0
    if pct > 0:
        return avg * pct / 100.0
    atr = _as_float(entry_atr) if entry_atr is not None else 0.0
    mult = _as_float(chandelier_multiplier)
    if atr > 0 and mult > 0:
        return atr * mult
    return 0.0


def profit_r_units(
    *,
    is_long: bool,
    avg_price: float,
    extreme: float,
    r_distance: float,
) -> float:
    if r_distance <= 0:
        return 0.0
    avg = _as_float(avg_price)
    ext = _as_float(extreme)
    if is_long:
        return max(0.0, (ext - avg) / r_distance)
    return max(0.0, (avg - ext) / r_distance)


def chandelier_kwargs_from_config(config: dict | None) -> dict[str, float]:
    cfg = config or {}
    return {
        "multiplier": max(0.5, min(10.0, _as_float(cfg.get("chandelier_multiplier"), 3.0))),
        "arm_r": max(0.0, min(5.0, _as_float(cfg.get("chandelier_arm_r"), 0.0))),
        "tighten_r": max(0.5, min(10.0, _as_float(cfg.get("chandelier_tighten_r"), 2.0))),
        "tighten_mult": max(0.5, min(10.0, _as_float(cfg.get("chandelier_tighten_mult"), 2.0))),
    }


def _candle_ts_seconds(candle: dict) -> float | None:
    raw = candle.get("time")
    if raw is None:
        raw = candle.get("timestamp")
    ts = _as_float(raw)
    if ts <= 0:
        return None
    if ts > 1e12:
        return ts / 1000.0
    return ts


def hold_excursion_from_candles(
    candles: list | None,
    *,
    opened_at: float | None = None,
) -> tuple[float | None, float | None]:
    """Max high / min low over bars in the hold (bar wicks, not last trade)."""
    opened = _as_float(opened_at) if opened_at is not None else 0.0
    hi = None
    lo = None
    for raw in candles or []:
        if not isinstance(raw, dict):
            continue
        if opened > 0:
            ts = _candle_ts_seconds(raw)
            # Keep the opening bar (time is usually the bar start).
            if ts is not None and ts + 60.0 < opened:
                continue
        h = _as_float(raw.get("high"))
        low = _as_float(raw.get("low"))
        if h > 0:
            hi = h if hi is None else max(hi, h)
        if low > 0:
            lo = low if lo is None else min(lo, low)
    return hi, lo


def ratchet_chandelier_stop(
    *,
    is_long: bool,
    avg_price: float,
    extreme: float,
    atr: float,
    current_sl: float | None,
    multiplier: float,
    arm_r: float = 0.0,
    tighten_r: float = 2.0,
    tighten_mult: float = 2.0,
    stop_loss_percent: float | None = None,
    entry_atr: float | None = None,
) -> float | None:
    """One-way chandelier / profit-floor update. Never loosens ``current_sl``."""
    avg = _as_float(avg_price)
    ext = _as_float(extreme)
    atr_f = _as_float(atr)
    if atr_f <= 0:
        atr_f = _as_float(entry_atr)
    mult = _as_float(multiplier, 3.0)
    arm = _as_float(arm_r)
    tight_r = _as_float(tighten_r, 2.0)
    tight_m = _as_float(tighten_mult, 2.0)

    if arm <= 0:
        if atr_f <= 0:
            return current_sl
        profit_atr = (ext - avg) / atr_f if is_long else (avg - ext) / atr_f
        eff = tight_m if profit_atr >= tight_r else mult
        potential = ext - eff * atr_f if is_long else ext + eff * atr_f
        if current_sl is None:
            return potential
        return max(current_sl, potential) if is_long else min(current_sl, potential)

    r_dist = resolve_r_distance(
        avg_price=avg,
        stop_loss_percent=stop_loss_percent,
        entry_atr=entry_atr if entry_atr is not None else atr_f,
        chandelier_multiplier=mult,
    )
    r_units = profit_r_units(
        is_long=is_long, avg_price=avg, extreme=ext, r_distance=r_dist,
    )
    if r_units < arm:
        return current_sl

    if is_long:
        sl = avg if current_sl is None else max(current_sl, avg)
        if atr_f > 0:
            eff = tight_m if r_units >= tight_r else mult
            sl = max(sl, ext - eff * atr_f)
        return sl

    sl = avg if current_sl is None else min(current_sl, avg)
    if atr_f > 0:
        eff = tight_m if r_units >= tight_r else mult
        sl = min(sl, ext + eff * atr_f)
    return sl
