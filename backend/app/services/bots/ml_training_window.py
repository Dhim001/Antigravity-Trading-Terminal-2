"""Map ML Lab ``training_window_months`` to candle fetch targets.

The Lab selector sends ``config.training_window_months`` (1 / 3 / 6 / 12).
Fetch helpers use this to size history and time-trim series so each choice
pulls a meaningfully different window (subject to archive/REST availability
and ``ML_TRAIN_CANDLE_MAX``).
"""

from __future__ import annotations

import os
import time
from typing import Any

# Soft per-window caps for 1m interactive train/validate (memory-safe).
# Ideal calendar sizes are much larger; caps keep Lab jobs responsive.
_WINDOW_BAR_CAP_1M: dict[int, int] = {
    1: 12_000,   # ~8–12 trading days of dense 1m if capped
    3: 25_000,
    6: 40_000,
    12: 50_000,
}

_DAYS_PER_MONTH = 30


def _hard_candle_max() -> int:
    return max(2_000, int(os.environ.get("ML_TRAIN_CANDLE_MAX", "50000")))


def parse_training_window_months(config: dict | None) -> int:
    """Return clamped months from config (default 3 to match Lab default)."""
    raw = (config or {}).get("training_window_months", 3)
    try:
        months = int(raw)
    except (TypeError, ValueError):
        months = 3
    if months not in (1, 3, 6, 12):
        # Nearest allowed bucket
        months = min((1, 3, 6, 12), key=lambda m: abs(m - months))
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
            target = min(target, max(2_500, ideal // 3), 12_000)
        return max(500, target)

    cap_1m = _WINDOW_BAR_CAP_1M.get(months, 25_000)
    cap = cap_1m
    if purpose == "validate":
        cap = int(cap * 1.2)
    return max(500, min(ideal, cap, hard))


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
    return {
        "training_window_months": months,
        "timeframe": tf,
        "bars": n,
        "bar_limit": bar_limit,
        "span_days": span_days,
        "from_ts": t0,
        "to_ts": t1,
    }
