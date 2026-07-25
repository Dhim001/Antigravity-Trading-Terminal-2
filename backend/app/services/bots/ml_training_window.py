"""Map ML Lab ``training_window_months`` to candle fetch targets.

The Lab selector sends ``config.training_window_months``
(1 / 3 / 6 / 12 / 18 / 24 / 36). Fetch helpers use this to size history and
time-trim series so each choice pulls a meaningfully different window
(subject to archive/REST availability and ``ML_TRAIN_CANDLE_MAX``).
"""

from __future__ import annotations

import os
import time
from typing import Any

# Allowed Lab window buckets (months).
TRAINING_WINDOW_MONTHS: tuple[int, ...] = (1, 3, 6, 12, 18, 24, 36)

# Soft per-window caps for 1m interactive train/validate (memory-safe).
# Ideal calendar sizes are much larger; caps keep Lab jobs responsive.
_WINDOW_BAR_CAP_1M: dict[int, int] = {
    1: 12_000,   # ~8–12 trading days of dense 1m if capped
    3: 25_000,
    6: 40_000,
    12: 50_000,
    18: 65_000,
    24: 80_000,
    36: 100_000,
}

_DAYS_PER_MONTH = 30


def _hard_candle_max() -> int:
    # Raised default so 24–36mo HT / denser 1m Lab windows are not crushed.
    return max(2_000, int(os.environ.get("ML_TRAIN_CANDLE_MAX", "100000")))


def parse_training_window_months(config: dict | None) -> int:
    """Return clamped months from config (default 3 to match Lab default)."""
    raw = (config or {}).get("training_window_months", 3)
    try:
        months = int(raw)
    except (TypeError, ValueError):
        months = 3
    if months not in TRAINING_WINDOW_MONTHS:
        # Nearest allowed bucket
        months = min(TRAINING_WINDOW_MONTHS, key=lambda m: abs(m - months))
    return months


def training_window_seconds(months: int) -> int:
    months = parse_training_window_months({"training_window_months": months})
    return int(months * _DAYS_PER_MONTH * 86400)


def bar_limit_for_training_window(
    months: int,
    *,
    timeframe: str = "1m",
    purpose: str = "train",
) -> int:
    """Target number of bars to request for the selected window.

    ``1m`` keeps memory-safe soft caps. Higher timeframes honor the calendar
    window up to ``ML_TRAIN_CANDLE_MAX`` so a Lab ``6 months · 5m`` choice is
    not silently crushed to a few thousand bars.
    """
    months = parse_training_window_months({"training_window_months": months})
    tf = str(timeframe or "1m").lower()
    secs = 60
    try:
        from app.services.market.timeframes import timeframe_to_secs

        secs = max(60, int(timeframe_to_secs(tf)))
    except Exception:
        pass

    ideal = int(training_window_seconds(months) / secs)
    hard = _hard_candle_max()
    if secs > 60:
        # HTF: Lab window ≈ calendar coverage (subject to archive/REST depth).
        target = min(ideal, hard)
        if purpose == "validate":
            # Interactive WF stays leaner than full Train, but still scales with window.
            lean_cap = 12_000 if months <= 12 else (18_000 if months <= 24 else 24_000)
            target = min(target, max(2_500, ideal // 3), lean_cap)
        return max(500, target)

    cap_1m = _WINDOW_BAR_CAP_1M.get(months, 25_000)
    cap = cap_1m
    if purpose == "validate":
        cap = int(cap * 1.2)
    return max(500, min(ideal, cap, hard))


def _tf_seconds(timeframe: str = "1m") -> int:
    tf = str(timeframe or "1m").lower()
    try:
        from app.services.market.timeframes import timeframe_to_secs

        return max(60, int(timeframe_to_secs(tf)))
    except Exception:
        return 60


def wf_adaptive_fold_mins(n_candles: int, timeframe: str = "1m") -> tuple[int, int]:
    """Return ``(min_train, min_test)`` for walk-forward fold generation.

    Dense 1m series keep the classic 200/100 floors. Higher TFs (esp. equity
    RTH) often land under 500 FIT bars after calendar holdout — lean the folds
    so validation can still run instead of hard-failing.
    """
    n = max(0, int(n_candles or 0))
    secs = _tf_seconds(timeframe)
    if secs < 300 and n >= 500:
        return 200, 100
    if secs >= 3600:
        return max(120, min(200, n // 3)), max(40, min(100, n // 8))
    if secs >= 900:
        return max(120, min(200, n // 3)), max(50, min(100, n // 7))
    if secs >= 300:
        return max(150, min(200, n // 3)), max(60, min(100, n // 6))
    return 200, 100


def validate_min_candles(timeframe: str = "1m", *, n_folds: int = 5) -> int:
    """Minimum bars before walk-forward + PBO is allowed to start.

    Legacy floor was a flat 500, which fails on equity HT (1h/4h) with a short
    Lab window — RTH only yields ~130 1h bars per month, and FIT trim after
    calendar holdout can leave ~300. Higher TFs use a lower floor aligned with
    lean ``wf_adaptive_fold_mins`` (train+test+purge).
    """
    folds = max(2, min(10, int(n_folds or 5)))
    secs = _tf_seconds(timeframe)
    # Default fold mins: train 200 + test 100 + purge ~30 → 330.
    wf_floor = 200 + 100 + 30
    if secs >= 3600:  # 1h+
        # Lean mins ≈ 80+40+30; keep a small margin for multi-fold layout.
        return max(220, 80 + 40 + 30 + folds * 10)
    if secs >= 900:  # 15m
        return max(280, 100 + 50 + 30)
    if secs >= 300:  # 5m
        return max(400, min(500, wf_floor))
    return max(500, wf_floor)


def validate_fetch_target_candles(timeframe: str = "1m", *, n_folds: int = 5) -> int:
    """Bars to aim for when expanding the Lab window before WF+PBO.

    ``validate_min_candles`` is the hard fail floor. The fetch target is higher
    so fold train windows still clear GBM/LSTM mins after purge + embargo
    (short 1h FIT series previously stopped expand at ~220 and then failed
    every fold with ``58 < 230``).
    """
    base = validate_min_candles(timeframe, n_folds=n_folds)
    folds = max(2, min(10, int(n_folds or 5)))
    secs = _tf_seconds(timeframe)
    # ~280 bars per fold covers train≥200 + test + purge slack on HT.
    per_fold = 280 if secs >= 900 else 400
    target = max(base, per_fold * folds)
    if secs >= 3600:
        return max(target, 800)
    if secs >= 900:
        return max(target, 600)
    return max(target, base)


def next_training_window_months(months: int) -> int | None:
    """Next larger Lab window bucket, or None if already at max."""
    months = parse_training_window_months({"training_window_months": months})
    for m in TRAINING_WINDOW_MONTHS:
        if m > months:
            return m
    return None


def skip_live_artifact_writes(config: dict | None) -> bool:
    """True when trainers must not overwrite the live Lab champion on disk.

    Walk-forward / interactive validate trains fold models for OOS scoring only.
    Writing those into the production model root made Lab status show a tiny
    fold sample count after a full Train.
    """
    cfg = config if isinstance(config, dict) else {}
    return bool(
        cfg.get("skip_onnx_export")
        or cfg.get("_wf_mode")
        or cfg.get("wf_mode")
    )


def trim_candles_to_training_window(
    candles: list[dict],
    months: int,
    *,
    now_ts: int | None = None,
) -> list[dict]:
    """Keep bars inside the last ``months`` calendar window (by bar time)."""
    if not candles:
        return []
    months = parse_training_window_months({"training_window_months": months})
    cutoff = int(now_ts if now_ts is not None else time.time()) - training_window_seconds(months)

    out: list[dict] = []
    for c in candles:
        try:
            t = int(c.get("time") or c.get("bar_time") or 0)
        except (TypeError, ValueError):
            continue
        if t >= cutoff:
            out.append(c)
    return out or list(candles)


def summarize_training_window(
    candles: list[dict],
    months: int,
    *,
    bar_limit: int | None = None,
    timeframe: str | None = None,
    calendar: dict | None = None,
) -> dict[str, Any]:
    """Small metadata blob for train/validate responses / UI."""
    months = parse_training_window_months({"training_window_months": months})
    n = len(candles or [])
    t0 = t1 = None
    if candles:
        try:
            t0 = int(candles[0].get("time") or 0) or None
            t1 = int(candles[-1].get("time") or 0) or None
        except (TypeError, ValueError, IndexError):
            pass
    span_days = None
    if t0 and t1 and t1 >= t0:
        span_days = round((t1 - t0) / 86400.0, 2)
    try:
        from app.services.bots.ml_model_artifacts import normalize_model_timeframe

        tf = normalize_model_timeframe(timeframe)
    except Exception:
        tf = "1m"
    out: dict[str, Any] = {
        "training_window_months": months,
        "timeframe": tf,
        "bars": n,
        "bar_limit": bar_limit,
        "span_days": span_days,
        "from_ts": t0,
        "to_ts": t1,
    }
    if calendar:
        try:
            from app.services.bots.ml_data_calendar import summarize_calendar_for_ui

            ui = summarize_calendar_for_ui(calendar)
            if ui:
                out["data_calendar"] = ui
                out["fit_end_ts"] = calendar.get("fit_end_ts")
                out["holdout_days"] = calendar.get("holdout_days")
        except Exception:
            pass
    return out
