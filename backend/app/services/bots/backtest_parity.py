"""Live-parity helpers for backtester (HTF bias, filter prep)."""

from __future__ import annotations

from typing import Any


def htf_bias_from_bars(bar_prev: dict, bar_last: dict) -> str:
    """Mirror BotManager._get_htf_bias using two closed HTF bars."""
    close_last = float(bar_last.get("close", 0) or 0)
    close_prev = float(bar_prev.get("close", 0) or 0)
    if close_last <= 0 or close_prev <= 0:
        return "NEUTRAL"

    high_last = float(bar_last.get("high", 0) or 0)
    low_last = float(bar_last.get("low", 0) or 0)
    high_prev = float(bar_prev.get("high", 0) or 0)
    low_prev = float(bar_prev.get("low", 0) or 0)

    if close_last > close_prev and low_last >= low_prev:
        return "BULL"
    if close_last < close_prev and high_last <= high_prev:
        return "BEAR"
    return "NEUTRAL"


def build_htf_bias_lookup(htf_candles: list[dict]) -> list[tuple[int, str]]:
    """Return [(bar_close_time, bias)] for each HTF bar with a prior bar."""
    if not htf_candles or len(htf_candles) < 3:
        return []
    rows: list[tuple[int, str]] = []
    for i in range(2, len(htf_candles)):
        bar_prev = htf_candles[i - 2]
        bar_last = htf_candles[i - 1]
        t = int(bar_last.get("time") or 0)
        if t <= 0:
            continue
        rows.append((t, htf_bias_from_bars(bar_prev, bar_last)))
    return rows


def htf_bias_at_time(lookup: list[tuple[int, str]], bar_time: int | None) -> str:
    if not lookup or bar_time is None:
        return "NEUTRAL"
    try:
        t = int(bar_time)
    except (TypeError, ValueError):
        return "NEUTRAL"
    bias = "NEUTRAL"
    for close_t, b in lookup:
        if close_t <= t:
            bias = b
        else:
            break
    return bias
