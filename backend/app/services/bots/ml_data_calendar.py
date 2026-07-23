"""ML Lab data calendar: FIT → EMBARGO → HOLDOUT (same symbol · TF).

When ``ML_CALENDAR_HOLDOUT`` is enabled, Lab Train / Validate use only the
FIT slice; Algo ML backtests default to the locked HOLDOUT trailing window.
"""

from __future__ import annotations

import os
import time
from typing import Any

CALENDAR_VERSION = 1

# Default holdout as a fraction of the Lab training window (clamped below).
_DEFAULT_HOLDOUT_FRAC = 0.15
_MIN_HOLDOUT_DAYS = 7
_MAX_HOLDOUT_DAYS = 30


def calendar_holdout_enabled(config: dict | None = None) -> bool:
    """True when nested FIT/EMBARGO/HOLDOUT is active.

    Env ``ML_CALENDAR_HOLDOUT=1`` (or true/yes) enables globally.
    Per-request override: ``config.ml_calendar_holdout`` bool.
    """
    cfg = config if isinstance(config, dict) else {}
    if "ml_calendar_holdout" in cfg:
        return bool(cfg.get("ml_calendar_holdout"))
    try:
        from app.config import ML_CALENDAR_HOLDOUT

        if ML_CALENDAR_HOLDOUT:
            return True
    except Exception:
        pass
    return os.environ.get("ML_CALENDAR_HOLDOUT", "").lower() in ("1", "true", "yes")


def default_holdout_days(months: int) -> int:
    """14–30d typical; 1-month Lab window floors at 7d."""
    try:
        m = int(months)
    except (TypeError, ValueError):
        m = 3
    raw = int(round(max(1, m) * 30 * _DEFAULT_HOLDOUT_FRAC))
    lo = 7 if m <= 1 else _MIN_HOLDOUT_DAYS
    return max(lo, min(_MAX_HOLDOUT_DAYS, raw))


def label_horizon_bars(config: dict | None = None) -> int:
    """Vertical / triple-barrier holding horizon used for purge/embargo."""
    cfg = config if isinstance(config, dict) else {}
    try:
        return max(1, int(cfg.get("triple_barrier_max_bars", 30)))
    except (TypeError, ValueError):
        return 30


def _bar_seconds(timeframe: str | None) -> int:
    tf = str(timeframe or "1m").lower()
    try:
        from app.services.market.timeframes import timeframe_to_secs

        return max(60, int(timeframe_to_secs(tf)))
    except Exception:
        return 60


def build_ml_data_calendar(
    *,
    months: int = 3,
    label_horizon_bars: int | None = None,
    holdout_days: int | None = None,
    timeframe: str = "1m",
    now_ts: int | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    """Build FIT / EMBARGO / HOLDOUT partition for a Lab training window.

    Returns timestamps (unix seconds) and bar counts suitable for artifact
    metadata and candle trimming.
    """
    from app.services.bots.ml_training_window import (
        parse_training_window_months,
        training_window_seconds,
    )

    months = parse_training_window_months({"training_window_months": months})
    cfg = config if isinstance(config, dict) else {}
    if label_horizon_bars is not None:
        try:
            horizon = max(1, int(label_horizon_bars))
        except (TypeError, ValueError):
            horizon = _label_horizon_from_cfg(cfg)
    else:
        horizon = _label_horizon_from_cfg(cfg)

    if holdout_days is None:
        raw_h = cfg.get("holdout_days")
        if raw_h is not None:
            try:
                holdout_days = int(raw_h)
            except (TypeError, ValueError):
                holdout_days = default_holdout_days(months)
        else:
            holdout_days = default_holdout_days(months)
    holdout_days = max(1, min(90, int(holdout_days)))

    now = int(now_ts if now_ts is not None else time.time())
    window_secs = training_window_seconds(months)
    window_start = now - window_secs
    bar_secs = _bar_seconds(timeframe)
    embargo_bars = max(horizon, 1)
    embargo_secs = embargo_bars * bar_secs
    holdout_secs = holdout_days * 86400

    holdout_end_ts = now
    holdout_start_ts = max(window_start, now - holdout_secs)
    # Embargo sits immediately before holdout; FIT ends before embargo.
    fit_end_ts = max(window_start, holdout_start_ts - embargo_secs)
    fit_start_ts = window_start

    # Guard: tiny windows — keep at least ~half the span for FIT.
    if fit_end_ts <= fit_start_ts:
        fit_end_ts = fit_start_ts + max(86400, window_secs // 2)
        holdout_start_ts = min(now, fit_end_ts + embargo_secs)
        holdout_end_ts = now

    return {
        "calendar_version": CALENDAR_VERSION,
        "training_window_months": months,
        "timeframe": str(timeframe or "1m"),
        "now_ts": now,
        "window_start_ts": int(fit_start_ts),
        "fit_start_ts": int(fit_start_ts),
        "fit_end_ts": int(fit_end_ts),
        "embargo_bars": int(embargo_bars),
        "embargo_secs": int(embargo_secs),
        "holdout_days": int(holdout_days),
        "holdout_start_ts": int(holdout_start_ts),
        "holdout_end_ts": int(holdout_end_ts),
        "label_horizon_bars": int(horizon),
        "purge_bars": int(embargo_bars),
        "enabled": True,
    }


def _label_horizon_from_cfg(cfg: dict) -> int:
    return label_horizon_bars(cfg)


def calendar_from_config(
    config: dict | None,
    *,
    months: int | None = None,
    timeframe: str | None = None,
    now_ts: int | None = None,
) -> dict[str, Any] | None:
    """Build calendar when holdout mode is on; else None."""
    if not calendar_holdout_enabled(config):
        return None
    cfg = dict(config or {})
    from app.services.bots.ml_training_window import parse_training_window_months

    win = months if months is not None else parse_training_window_months(cfg)
    tf = timeframe or cfg.get("timeframe") or "1m"
    return build_ml_data_calendar(
        months=win,
        timeframe=str(tf),
        now_ts=now_ts,
        config=cfg,
    )


def _bar_time(c: dict) -> int:
    try:
        return int(c.get("time") or c.get("bar_time") or 0)
    except (TypeError, ValueError):
        return 0


def trim_candles_to_fit(
    candles: list[dict],
    calendar: dict[str, Any] | None,
) -> list[dict]:
    """Keep bars with time in [fit_start, fit_end] inclusive."""
    if not candles or not calendar:
        return list(candles or [])
    t0 = int(calendar.get("fit_start_ts") or 0)
    t1 = int(calendar.get("fit_end_ts") or 0)
    if t1 <= 0:
        return list(candles)
    out = [c for c in candles if t0 <= _bar_time(c) <= t1]
    return out or list(candles)


def trim_candles_to_holdout(
    candles: list[dict],
    calendar: dict[str, Any] | None,
) -> list[dict]:
    """Keep bars with time in [holdout_start, holdout_end]."""
    if not candles or not calendar:
        return list(candles or [])
    t0 = int(calendar.get("holdout_start_ts") or 0)
    t1 = int(calendar.get("holdout_end_ts") or 0)
    if t1 <= 0:
        return list(candles)
    out = [c for c in candles if t0 <= _bar_time(c) <= t1]
    return out or list(candles)


def backtest_in_sample(
    calendar: dict[str, Any] | None,
    *,
    from_ts: int | None,
    to_ts: int | None,
) -> bool:
    """True when the BT window overlaps FIT (or embargo) rather than holdout-only."""
    if not calendar or not from_ts or not to_ts:
        return False
    hold_start = int(calendar.get("holdout_start_ts") or 0)
    fit_end = int(calendar.get("fit_end_ts") or 0)
    if hold_start <= 0:
        return False
    # Overlaps anything before holdout_start → in-sample (or embargo peek).
    if int(from_ts) < hold_start:
        return True
    # Entirely after fit_end and overlapping holdout is OOS.
    if int(to_ts) <= fit_end:
        return True
    return False


def merge_calendar_into_metadata(
    metadata: dict[str, Any] | None,
    calendar: dict[str, Any] | None,
) -> dict[str, Any]:
    """Attach calendar keys onto a train metadata dict (in place + return)."""
    meta = dict(metadata or {})
    if not calendar:
        return meta
    cal = {
        "calendar_version": calendar.get("calendar_version", CALENDAR_VERSION),
        "fit_start_ts": calendar.get("fit_start_ts"),
        "fit_end_ts": calendar.get("fit_end_ts"),
        "embargo_bars": calendar.get("embargo_bars"),
        "holdout_days": calendar.get("holdout_days"),
        "holdout_start_ts": calendar.get("holdout_start_ts"),
        "holdout_end_ts": calendar.get("holdout_end_ts"),
        "label_horizon_bars": calendar.get("label_horizon_bars"),
        "purge_bars": calendar.get("purge_bars"),
        "training_window_months": calendar.get("training_window_months"),
        "timeframe": calendar.get("timeframe"),
    }
    meta["data_calendar"] = cal
    # Flat aliases for deploy / BT helpers that prefer top-level keys.
    meta["fit_end_ts"] = cal["fit_end_ts"]
    meta["holdout_start_ts"] = cal["holdout_start_ts"]
    meta["holdout_end_ts"] = cal["holdout_end_ts"]
    meta["holdout_days"] = cal["holdout_days"]
    meta["calendar_version"] = cal["calendar_version"]
    return meta


def load_data_calendar_from_metadata(meta: dict | None) -> dict[str, Any] | None:
    """Normalize calendar from artifact metadata.json."""
    if not isinstance(meta, dict):
        return None
    nested = meta.get("data_calendar")
    if isinstance(nested, dict) and nested.get("fit_end_ts"):
        return dict(nested)
    if meta.get("fit_end_ts") and meta.get("holdout_start_ts"):
        return {
            "calendar_version": meta.get("calendar_version", CALENDAR_VERSION),
            "fit_end_ts": meta.get("fit_end_ts"),
            "fit_start_ts": meta.get("fit_start_ts"),
            "holdout_start_ts": meta.get("holdout_start_ts"),
            "holdout_end_ts": meta.get("holdout_end_ts"),
            "holdout_days": meta.get("holdout_days"),
            "embargo_bars": meta.get("embargo_bars"),
            "purge_bars": meta.get("purge_bars"),
            "label_horizon_bars": meta.get("label_horizon_bars"),
        }
    return None


def resolve_strategy_data_calendar(
    strategy: str,
    symbol: str,
    *,
    timeframe: str | None = None,
    config: dict | None = None,
) -> dict[str, Any] | None:
    """Load calendar from live model metadata, else build from config when enabled."""
    if not calendar_holdout_enabled(config):
        return None
    try:
        from app.services.bots.ml_walk_forward_validator import is_ml_strategy

        if not is_ml_strategy(strategy):
            return None
    except Exception:
        return None

    cfg = dict(config or {})
    tf = timeframe or cfg.get("timeframe") or "1m"
    try:
        from app.services.bots.ml_model_artifacts import model_root_for
        import json
        import os

        root = model_root_for(strategy, symbol, tf)
        if root:
            path = os.path.join(root, "metadata.json")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as fh:
                    meta = json.load(fh)
                cal = load_data_calendar_from_metadata(meta if isinstance(meta, dict) else None)
                if cal:
                    return cal
    except Exception:
        pass

    return calendar_from_config(cfg, timeframe=str(tf))


def summarize_calendar_for_ui(calendar: dict[str, Any] | None) -> dict[str, Any] | None:
    """Compact blob for Lab responses / strip."""
    if not calendar:
        return None
    fit_days = None
    hold_days = calendar.get("holdout_days")
    try:
        fs = int(calendar["fit_start_ts"])
        fe = int(calendar["fit_end_ts"])
        if fe > fs:
            fit_days = round((fe - fs) / 86400.0, 1)
    except (KeyError, TypeError, ValueError):
        pass
    return {
        "calendar_version": calendar.get("calendar_version"),
        "fit_days": fit_days,
        "embargo_bars": calendar.get("embargo_bars"),
        "holdout_days": hold_days,
        "fit_end_ts": calendar.get("fit_end_ts"),
        "holdout_start_ts": calendar.get("holdout_start_ts"),
        "holdout_end_ts": calendar.get("holdout_end_ts"),
    }
