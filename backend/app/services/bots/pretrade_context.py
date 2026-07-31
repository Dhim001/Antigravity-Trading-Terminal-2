"""Shared Pre-Trade streak / cool-down context for live, backtest, ML, and agents.

``failures_streak`` is treated as adaptive risk by default (REDUCE_SIZE), not a
hard kill — backtests show strategies can remain profitable after consecutive
losing runs. Hard VETO remains for structural risks (events, gaps, anomalies).
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from app.config import (
    BOT_MAX_CONSECUTIVE_LOSSES,
    PRETRADE_SETUP_FAIL_LIMIT,
    PRETRADE_SETUP_LOOKBACK_HOURS,
    PRETRADE_STREAK_COOLDOWN_SEC,
    PRETRADE_STREAK_MODE,
    PRETRADE_STREAK_REDUCE_FACTOR,
    PRETRADE_STREAK_SEVERE_FACTOR,
    PRETRADE_STREAK_SEVERE_LIMIT,
)


STREAK_MODES = frozenset({"reduce", "veto", "off"})


def resolve_streak_mode(bot_config: dict | None = None) -> str:
    cfg = bot_config or {}
    raw = str(cfg.get("pretrade_streak_mode") or PRETRADE_STREAK_MODE or "reduce").lower()
    return raw if raw in STREAK_MODES else "reduce"


def streak_reduce_factor(bot_config: dict | None = None) -> float:
    cfg = bot_config or {}
    try:
        return float(cfg.get("pretrade_streak_reduce_factor", PRETRADE_STREAK_REDUCE_FACTOR))
    except (TypeError, ValueError):
        return float(PRETRADE_STREAK_REDUCE_FACTOR)


def streak_severe_factor(bot_config: dict | None = None) -> float:
    cfg = bot_config or {}
    try:
        return float(cfg.get("pretrade_streak_severe_factor", PRETRADE_STREAK_SEVERE_FACTOR))
    except (TypeError, ValueError):
        return float(PRETRADE_STREAK_SEVERE_FACTOR)


def streak_severe_limit(bot_config: dict | None = None) -> int:
    cfg = bot_config or {}
    try:
        return max(1, int(cfg.get("pretrade_streak_severe_limit", PRETRADE_STREAK_SEVERE_LIMIT)))
    except (TypeError, ValueError):
        return int(PRETRADE_STREAK_SEVERE_LIMIT)


def streak_cooldown_sec(bot_config: dict | None = None) -> int:
    cfg = bot_config or {}
    try:
        return max(0, int(cfg.get("pretrade_streak_cooldown_sec", PRETRADE_STREAK_COOLDOWN_SEC)))
    except (TypeError, ValueError):
        return int(PRETRADE_STREAK_COOLDOWN_SEC)


def consecutive_loss_count(exit_pnls: Sequence[float], *, newest_first: bool = False) -> int:
    """Count consecutive losing exits from the most recent trade."""
    pnls = [float(p) for p in (exit_pnls or [])]
    if newest_first:
        seq = pnls
    else:
        seq = list(reversed(pnls))
    streak = 0
    for p in seq:
        if p < 0.0:
            streak += 1
        else:
            break
    return streak


def apply_failures_streak(
    exit_pnls: Sequence[float] | None,
    *,
    bot_config: dict | None = None,
    setup_fail_limit: int | None = None,
    newest_first: bool = False,
    lookback_hours: float | None = None,
) -> dict[str, Any] | None:
    """Evaluate streak policy.

    Returns None when no streak action applies, else a dict with
    ``verdict`` (REDUCE_SIZE|VETO), ``size_multiplier``, ``reason``, ``streak``,
    ``vetoes`` entry string.
    """
    mode = resolve_streak_mode(bot_config)
    if mode == "off" or exit_pnls is None:
        return None

    fail_limit = int(
        setup_fail_limit
        if setup_fail_limit is not None
        else (bot_config or {}).get("pretrade_setup_fail_limit", PRETRADE_SETUP_FAIL_LIMIT)
    )
    if fail_limit <= 0:
        return None

    pnls = [float(p) for p in exit_pnls]
    streak = consecutive_loss_count(pnls, newest_first=newest_first)
    if streak < fail_limit:
        return None

    # Window of last fail_limit exits (all must be losses for the classic rule).
    if newest_first:
        window = pnls[:fail_limit]
    else:
        window = pnls[-fail_limit:] if len(pnls) >= fail_limit else pnls
    if len(window) < fail_limit or not all(p < 0.0 for p in window):
        # Still have a consecutive streak from newest, but mixed older window —
        # act on consecutive streak count alone when at/above limit.
        if streak < fail_limit:
            return None

    hours = float(
        lookback_hours
        if lookback_hours is not None
        else PRETRADE_SETUP_LOOKBACK_HOURS
    )
    reason = f"{streak} losses in last {hours}h"
    veto_line = f"failures_streak: {reason}"

    # Escalate to hard VETO only in explicit veto mode, or when streak reaches
    # the Risk Sentinel max (one owner for "stop trading" above that).
    cfg = bot_config or {}
    try:
        max_streak = int(cfg.get("max_consecutive_losses", BOT_MAX_CONSECUTIVE_LOSSES))
    except (TypeError, ValueError):
        max_streak = int(BOT_MAX_CONSECUTIVE_LOSSES)

    if mode == "veto" or (max_streak > 0 and streak >= max_streak):
        return {
            "verdict": "VETO",
            "size_multiplier": 0.0,
            "reason": reason,
            "streak": streak,
            "vetoes": [veto_line],
            "cooldown_sec": streak_cooldown_sec(bot_config),
        }

    # Stepped reduce: mild at fail_limit, severe at severe_limit+.
    severe_at = streak_severe_limit(bot_config)
    if streak >= severe_at:
        mult = streak_severe_factor(bot_config)
    else:
        mult = streak_reduce_factor(bot_config)
    mult = max(0.05, min(1.0, float(mult)))
    return {
        "verdict": "REDUCE_SIZE",
        "size_multiplier": mult,
        "reason": reason,
        "streak": streak,
        "vetoes": [veto_line],
        "cooldown_sec": streak_cooldown_sec(bot_config),
    }


def empty_trade_state() -> dict[str, Any]:
    return {
        "consecutive_losses": 0,
        "losses_in_lookback": 0,
        "last_pretrade_verdict": None,
        "streak_size_mult": 1.0,
        "cool_until_ts": None,
        "bot_loss_streak": 0.0,
        "bot_win_rate_24h": 0.5,
        # Normalized 0–1 (same as build_trade_state_snapshot): 24h / 72h.
        "hours_since_last_loss": 24.0 / 72.0,
    }


def build_trade_state_snapshot(
    *,
    consecutive_losses: int = 0,
    losses_in_lookback: int = 0,
    last_pretrade_verdict: str | None = None,
    streak_size_mult: float = 1.0,
    cool_until_ts: float | None = None,
    win_rate_24h: float | None = None,
    hours_since_last_loss: float | None = None,
) -> dict[str, Any]:
    """Normalized snapshot for signal_data / agent / ML trade-state features."""
    streak = max(0, int(consecutive_losses))
    # Bounded ML features (0–1 or hours capped).
    bot_loss_streak = min(1.0, streak / 10.0)
    wr = 0.5 if win_rate_24h is None else max(0.0, min(1.0, float(win_rate_24h)))
    hrs = 24.0 if hours_since_last_loss is None else max(0.0, min(72.0, float(hours_since_last_loss)))
    return {
        "consecutive_losses": streak,
        "losses_in_lookback": max(0, int(losses_in_lookback)),
        "last_pretrade_verdict": last_pretrade_verdict,
        "streak_size_mult": float(streak_size_mult),
        "cool_until_ts": cool_until_ts,
        "bot_loss_streak": bot_loss_streak,
        "bot_win_rate_24h": wr,
        "hours_since_last_loss": hrs / 72.0,  # normalize 0–1 for ML
    }


def suggest_streak_thresholds_from_backtest(summary: dict | None) -> dict[str, Any]:
    """Suggest Pre-Trade / Risk streak knobs from a backtest summary.

    Uses empirical ``max_consecutive_losses`` so operators do not rely on a
    magic ``3`` that fights profitable strategies with deep but recoverable runs.
    """
    summary = summary if isinstance(summary, dict) else {}
    raw = summary.get("max_consecutive_losses")
    try:
        max_streak = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        max_streak = None
    if max_streak is None or max_streak < 1:
        return {
            "ok": False,
            "error": "backtest summary missing max_consecutive_losses",
            "suggested": None,
        }
    # Start reducing before the historical max; hard pause near/at max.
    fail_limit = max(2, min(max_streak, max(3, max_streak // 2)))
    severe = max(fail_limit + 1, min(max_streak, fail_limit + 2))
    sentinel = max(severe, max_streak)
    return {
        "ok": True,
        "observed_max_consecutive_losses": max_streak,
        "suggested": {
            "pretrade_streak_mode": "reduce",
            "pretrade_setup_fail_limit": fail_limit,
            "pretrade_streak_severe_limit": severe,
            "max_consecutive_losses": sentinel,
            "rationale": (
                f"Backtest max consecutive losses was {max_streak}. "
                "Reduce size from "
                f"{fail_limit} losses; deeper cut at {severe}; "
                f"Risk Sentinel pause at {sentinel}."
            ),
        },
    }


def is_cool_until_active(cool_until_ts: float | None, *, now: float | None = None) -> bool:
    if cool_until_ts is None:
        return False
    try:
        until = float(cool_until_ts)
    except (TypeError, ValueError):
        return False
    return until > float(now if now is not None else time.time())


def set_bot_streak_cooldown(bot: dict, cooldown_sec: int, *, now: float | None = None) -> float | None:
    """Arm a short entry cool-down on the live bot dict. Returns cool_until_ts."""
    if cooldown_sec <= 0:
        return bot.get("_pretrade_streak_cool_until")
    ts = float(now if now is not None else time.time()) + int(cooldown_sec)
    prev = bot.get("_pretrade_streak_cool_until")
    try:
        prev_f = float(prev) if prev is not None else 0.0
    except (TypeError, ValueError):
        prev_f = 0.0
    # Do not shorten an existing cool-down.
    bot["_pretrade_streak_cool_until"] = max(prev_f, ts)
    return bot["_pretrade_streak_cool_until"]


def get_bot_streak_cooldown_hold(bot: dict, *, now: float | None = None) -> dict[str, Any] | None:
    """UI / risk hold payload for Pre-Trade streak cool-down."""
    until = bot.get("_pretrade_streak_cool_until")
    if not is_cool_until_active(until, now=now):
        return None
    now_f = float(now if now is not None else time.time())
    until_f = float(until)
    remaining = max(0, int(until_f - now_f))
    from datetime import datetime, timezone

    until_iso = datetime.fromtimestamp(until_f, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    streak = int(bot.get("_pretrade_streak_count") or 0)
    return {
        "kind": "pretrade_streak",
        "consecutive_losses": streak,
        "remaining_sec": remaining,
        "cooloff_sec": remaining,
        "cooloff_until": until_iso,
        "reason": f"Pre-Trade streak cool-down ({streak} losses)",
        "block_reason": (
            f"Pre-Trade streak cool-down: {remaining}s remaining after "
            f"{streak} consecutive losses — size reduced / entries paused briefly."
        ),
    }


def apply_reduce_size_multiplier(
    quantity: float,
    size_multiplier: float,
    *,
    vetoes: Sequence[Any] | None = None,
    recent_closed_pnls: Sequence[float] | None = None,
    use_regime_sizing: bool = True,
) -> tuple[float, str]:
    """Apply PreTrade ``REDUCE_SIZE`` without double-halving vs regime sizing.

    Live and backtest both call ``scale_entry_quantity`` (regime ×0.5 on 3
    losses) then PreTrade streak reduce. When the reduce is a ``failures_streak``
    cut at the same ×0.5 floor, skip a second multiply; only apply an extra
    factor when the streak multiplier is stricter than 0.5 (severe step).
    Non-streak reduces (e.g. sentiment) always multiply in full.
    """
    qty = float(quantity or 0.0)
    mult = float(size_multiplier)
    if qty <= 0:
        return 0.0, "×0"
    if mult <= 0:
        return 0.0, "×0"
    if mult >= 1.0 - 1e-12:
        return qty, "×1.00"

    streak_reduce = any(
        str(v).startswith("failures_streak") for v in (vetoes or [])
    )
    pnls = [float(p) for p in (recent_closed_pnls or [])]
    regime_already = (
        bool(use_regime_sizing)
        and len(pnls) >= 3
        and all(p < 0 for p in pnls[-3:])
    )
    if streak_reduce and regime_already:
        if mult < 0.5 - 1e-9:
            qty *= mult / 0.5
            return qty, f"extra streak cut → ×{mult:.2f} total"
        return qty, "streak align (regime sizing already ×0.5)"
    qty *= mult
    return qty, f"×{mult:.2f}"


def prefer_hold_on_streak(
    signal: str | None,
    signal_data: dict | None,
    *,
    bot_config: dict | None = None,
    consecutive_losses: int = 0,
    cool_until_ts: float | None = None,
) -> tuple[str | None, dict]:
    """Suppress entries while a Pre-Trade streak cool-down is already armed.

    Streak count alone must NOT hard-HOLD: that permanently freezes the bot
    (no entry → no win to clear the streak). Size adaptation is PreTrade
    ``REDUCE_SIZE``; this helper only stops spam during the cool-down window.
    Streak state is still injected into ``signal_data`` for agent/ML awareness.
    """
    from app.config import PRETRADE_AWARE_SIGNALS

    data = dict(signal_data or {}) if isinstance(signal_data, dict) else {}
    cfg = bot_config or {}
    aware = cfg.get("pretrade_aware_signals")
    if aware is None:
        aware = PRETRADE_AWARE_SIGNALS
    if not aware:
        return signal, data

    sig = str(signal or data.get("signal") or "NONE").upper()
    if sig not in ("BUY", "SELL"):
        return signal, data

    if is_cool_until_active(cool_until_ts):
        data["signal"] = "NONE"
        data["raw_signal"] = sig
        data["reject_reason"] = "pretrade_streak_aware"
        data["reject_detail"] = (
            f"HOLD: Pre-Trade streak cool-down active "
            f"(streak {int(consecutive_losses)})"
        )
        return None, data
    return signal, data
