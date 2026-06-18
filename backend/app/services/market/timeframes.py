"""Canonical trading timeframes — aligned with ChartWidget TF_CONFIGS."""

from __future__ import annotations

# Normalized key -> bar length in seconds (matches frontend ChartWidget.jsx)
TIMEFRAME_SECS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

_ALIASES: dict[str, str] = {
    "1min": "1m",
    "5min": "5m",
    "15min": "15m",
    "1hour": "1h",
    "4hour": "4h",
    "1day": "1d",
    "1H": "1h",
    "4H": "4h",
    "1D": "1d",
}

_TICK_TF = "tick"


def normalize_timeframe(tf: str) -> str:
    """Return canonical lowercase timeframe key."""
    if not tf or not isinstance(tf, str):
        raise ValueError("timeframe is required")
    raw = tf.strip()
    if not raw:
        raise ValueError("timeframe is required")
    if raw.lower() == _TICK_TF:
        return _TICK_TF
    key = _ALIASES.get(raw, raw.lower())
    if key not in TIMEFRAME_SECS:
        raise ValueError(f"unsupported timeframe: {tf}")
    return key


def is_valid_timeframe(tf: str) -> bool:
    try:
        normalize_timeframe(tf)
        return True
    except ValueError:
        return False


def timeframe_to_secs(tf: str) -> int:
    """Bar interval in seconds for a normalized or alias timeframe."""
    key = normalize_timeframe(tf)
    if key == _TICK_TF:
        raise ValueError("tick timeframe has no bar interval")
    return TIMEFRAME_SECS[key]
