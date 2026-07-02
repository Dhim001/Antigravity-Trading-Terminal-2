"""Bar timestamp helpers for chart analyst / CHART_AGENT alignment."""

from __future__ import annotations

from app.services.market.timeframes import normalize_timeframe, timeframe_to_secs


def coerce_bar_time(value) -> int | None:
    if value is None:
        return None
    try:
        ts = int(float(value))
    except (TypeError, ValueError):
        return None
    if ts > 1_000_000_000_000:
        ts //= 1000
    return ts


def bar_times_match(left, right) -> bool:
    a = coerce_bar_time(left)
    b = coerce_bar_time(right)
    return a is not None and b is not None and a == b


def find_bar_index(df, bar_time) -> int | None:
    """Return the last row index whose time matches bar_time."""
    target = coerce_bar_time(bar_time)
    if target is None or df is None or df.empty or "time" not in df.columns:
        return None
    for idx in range(len(df) - 1, -1, -1):
        if coerce_bar_time(df.iloc[idx].get("time")) == target:
            return idx
    return None


def median_bar_gap_secs(candles: list[dict], *, sample: int = 12) -> float | None:
    if not candles or len(candles) < 3:
        return None
    tail = candles[-sample:] if len(candles) > sample else candles
    times: list[int] = []
    for bar in tail:
        ts = coerce_bar_time(bar.get("time"))
        if ts is not None:
            times.append(ts)
    if len(times) < 3:
        return None
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1) if times[i + 1] > times[i]]
    if not gaps:
        return None
    gaps.sort()
    return float(gaps[len(gaps) // 2])


def candles_match_timeframe(candles: list[dict], timeframe: str) -> bool:
    """Reject 1m-spaced series when scoring a higher timeframe insight."""
    tf = normalize_timeframe(timeframe) if timeframe and timeframe != "tick" else "1m"
    expected = timeframe_to_secs(tf)
    if expected <= 60:
        return True
    gap = median_bar_gap_secs(candles)
    if gap is None:
        return True
    return gap >= expected * 0.85
