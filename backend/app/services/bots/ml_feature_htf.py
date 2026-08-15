"""Multi-timeframe confluence features for the ML signal schema.

Train and live inference both resample the source candle buffer causally
(forming HTF bucket uses only bars seen so far) so values stay train/serve-aligned.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.services.market.timeframes import timeframe_to_secs


HTF_FEATURE_NAMES: tuple[str, ...] = (
    "htf_trend_1h",
    "htf_rsi_1h",
    "htf_atr_ratio_4h",
    "htf_regime_daily",
)

# Bars retained on the live path for HTF resampling (1m ≈ 25h).
HTF_HISTORY_LOOKBACK = 1500


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    if np.isnan(f) or np.isinf(f):
        return default
    return f


def _bar_unix(bar: dict) -> float | None:
    raw = bar.get("time")
    if raw is None:
        return None
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:
        ts /= 1000.0
    return ts


def _ema(series: np.ndarray, span: int) -> np.ndarray:
    n = len(series)
    out = np.zeros(n, dtype=np.float64)
    if n == 0 or span <= 1:
        return series.astype(np.float64, copy=True)
    alpha = 2.0 / (span + 1.0)
    out[0] = series[0]
    for i in range(1, n):
        out[i] = alpha * series[i] + (1.0 - alpha) * out[i - 1]
    return out


def _rsi(close: np.ndarray, length: int = 14) -> np.ndarray:
    n = len(close)
    out = np.full(n, 50.0, dtype=np.float64)
    if n < 2:
        return out
    delta = np.diff(close, prepend=close[0])
    gain = np.maximum(delta, 0.0)
    loss = np.maximum(-delta, 0.0)
    avg_gain = _ema(gain, length)
    avg_loss = _ema(loss, length)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_loss > 1e-12, avg_gain / avg_loss, 0.0)
        out = 100.0 - (100.0 / (1.0 + rs))
    return np.nan_to_num(out, nan=50.0, posinf=100.0, neginf=0.0)


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14) -> np.ndarray:
    n = len(close)
    tr = np.zeros(n, dtype=np.float64)
    if n == 0:
        return tr
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    return _ema(tr, length)


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14) -> np.ndarray:
    n = len(close)
    out = np.zeros(n, dtype=np.float64)
    if n < length + 1:
        return out
    up = np.zeros(n, dtype=np.float64)
    down = np.zeros(n, dtype=np.float64)
    for i in range(1, n):
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]
        up[i] = up_move if up_move > down_move and up_move > 0 else 0.0
        down[i] = down_move if down_move > up_move and down_move > 0 else 0.0
    atr = _atr(high, low, close, length)
    plus_dm = _ema(up, length)
    minus_dm = _ema(down, length)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = np.where(atr > 1e-12, 100.0 * plus_dm / atr, 0.0)
        minus_di = np.where(atr > 1e-12, 100.0 * minus_dm / atr, 0.0)
        denom = plus_di + minus_di
        dx = np.where(denom > 1e-12, 100.0 * np.abs(plus_di - minus_di) / denom, 0.0)
    return _ema(dx, length)


def _infer_source_secs(candles: list[dict], timeframe: str | None) -> int:
    if timeframe:
        try:
            return int(timeframe_to_secs(timeframe))
        except Exception:
            pass
    times = []
    for b in candles[:8]:
        t = _bar_unix(b) if isinstance(b, dict) else None
        if t is not None:
            times.append(t)
    if len(times) >= 2:
        diffs = [times[i] - times[i - 1] for i in range(1, len(times)) if times[i] > times[i - 1]]
        if diffs:
            return max(1, int(round(float(np.median(diffs)))))
    return 60


def _fields(bar: dict) -> dict[str, float] | None:
    try:
        return {
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": float(bar.get("volume") or 0),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _htf_indicator_snapshot(bars: list[dict], kind: str) -> float:
    """Last causal indicator value on an HTF series (completed + forming)."""
    if len(bars) < 1:
        return 0.0 if kind != "rsi" else 0.5
    o = np.array([b["open"] for b in bars], dtype=np.float64)
    h = np.array([b["high"] for b in bars], dtype=np.float64)
    l = np.array([b["low"] for b in bars], dtype=np.float64)
    c = np.array([b["close"] for b in bars], dtype=np.float64)
    if kind == "trend":
        if len(bars) < 2:
            return 0.0
        return float(np.sign(_ema(c, 9)[-1] - _ema(c, 21)[-1]))
    if kind == "rsi":
        return float(_rsi(c, 14)[-1] / 100.0)
    if kind == "atr_ratio":
        if len(bars) < 2:
            return 0.0
        atr = _atr(h, l, c, 14)[-1]
        return float(atr / c[-1]) if c[-1] > 0 else 0.0
    if kind == "regime":
        if len(bars) < 2:
            return 0.0
        return 1.0 if _adx(h, l, c, 14)[-1] > 25.0 else 0.0
    return 0.0


class _Ema:
    """Standard EMA; ``copy`` is O(1) so forming-bar peeks don't mutate committed state."""

    __slots__ = ("alpha", "value")

    def __init__(self, span: int):
        self.alpha = 2.0 / (span + 1.0)
        self.value = None

    def commit(self, x: float) -> float:
        x = float(x)
        self.value = x if self.value is None else self.alpha * x + (1.0 - self.alpha) * self.value
        return self.value

    def copy(self) -> "_Ema":
        other = _Ema.__new__(_Ema)
        other.alpha = self.alpha
        other.value = self.value
        return other


class _TrendEngine:
    __slots__ = ("ema9", "ema21", "n", "last")

    def __init__(self):
        self.ema9 = _Ema(9)
        self.ema21 = _Ema(21)
        self.n = 0
        self.last = 0.0

    def commit(self, bar: dict) -> None:
        close = float(bar["close"])
        self.ema9.commit(close)
        self.ema21.commit(close)
        self.n += 1
        self.last = 0.0 if self.n < 2 else float(np.sign(self.ema9.value - self.ema21.value))

    def copy(self) -> "_TrendEngine":
        other = _TrendEngine.__new__(_TrendEngine)
        other.ema9 = self.ema9.copy()
        other.ema21 = self.ema21.copy()
        other.n = self.n
        other.last = self.last
        return other


class _RsiEngine:
    __slots__ = ("gain", "loss", "prev_close", "n", "last")

    def __init__(self, length: int = 14):
        self.gain = _Ema(length)
        self.loss = _Ema(length)
        self.prev_close = None
        self.n = 0
        self.last = 0.5

    def commit(self, bar: dict) -> None:
        close = float(bar["close"])
        delta = 0.0 if self.prev_close is None else close - self.prev_close
        avg_gain = self.gain.commit(max(delta, 0.0))
        avg_loss = self.loss.commit(max(-delta, 0.0))
        self.prev_close = close
        self.n += 1
        if self.n < 2:
            self.last = 0.5
            return
        rs = (avg_gain / avg_loss) if avg_loss > 1e-12 else 0.0
        rsi = 100.0 - (100.0 / (1.0 + rs))
        if not np.isfinite(rsi):
            rsi = 50.0
        self.last = float(np.clip(rsi, 0.0, 100.0) / 100.0)

    def copy(self) -> "_RsiEngine":
        other = _RsiEngine.__new__(_RsiEngine)
        other.gain = self.gain.copy()
        other.loss = self.loss.copy()
        other.prev_close = self.prev_close
        other.n = self.n
        other.last = self.last
        return other


class _AtrRatioEngine:
    __slots__ = ("tr_ema", "prev_close", "n", "last")

    def __init__(self, length: int = 14):
        self.tr_ema = _Ema(length)
        self.prev_close = None
        self.n = 0
        self.last = 0.0

    def commit(self, bar: dict) -> None:
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        if self.prev_close is None:
            tr = high - low
        else:
            prev = self.prev_close
            tr = max(high - low, abs(high - prev), abs(low - prev))
        atr = self.tr_ema.commit(tr)
        self.prev_close = close
        self.n += 1
        if self.n < 2:
            self.last = 0.0
        else:
            self.last = float(atr / close) if close > 0 else 0.0

    def copy(self) -> "_AtrRatioEngine":
        other = _AtrRatioEngine.__new__(_AtrRatioEngine)
        other.tr_ema = self.tr_ema.copy()
        other.prev_close = self.prev_close
        other.n = self.n
        other.last = self.last
        return other


class _AdxRegimeEngine:
    """Wilder-style ADX via EMA; reports 0 until length+1 bars (matches ``_adx``)."""

    __slots__ = (
        "length", "tr_ema", "plus_ema", "minus_ema", "dx_ema",
        "prev_h", "prev_l", "prev_c", "n", "last",
    )

    def __init__(self, length: int = 14):
        self.length = length
        self.tr_ema = _Ema(length)
        self.plus_ema = _Ema(length)
        self.minus_ema = _Ema(length)
        self.dx_ema = _Ema(length)
        self.prev_h = None
        self.prev_l = None
        self.prev_c = None
        self.n = 0
        self.last = 0.0

    def commit(self, bar: dict) -> None:
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        if self.prev_c is None:
            tr = high - low
            up = 0.0
            down = 0.0
        else:
            tr = max(high - low, abs(high - self.prev_c), abs(low - self.prev_c))
            up_move = high - self.prev_h
            down_move = self.prev_l - low
            up = up_move if up_move > down_move and up_move > 0 else 0.0
            down = down_move if down_move > up_move and down_move > 0 else 0.0
        atr = self.tr_ema.commit(tr)
        plus_dm = self.plus_ema.commit(up)
        minus_dm = self.minus_ema.commit(down)
        plus_di = (100.0 * plus_dm / atr) if atr > 1e-12 else 0.0
        minus_di = (100.0 * minus_dm / atr) if atr > 1e-12 else 0.0
        denom = plus_di + minus_di
        dx = (100.0 * abs(plus_di - minus_di) / denom) if denom > 1e-12 else 0.0
        adx = self.dx_ema.commit(dx)
        self.prev_h, self.prev_l, self.prev_c = high, low, close
        self.n += 1
        if self.n < 2 or self.n < self.length + 1:
            self.last = 0.0
        else:
            self.last = 1.0 if adx > 25.0 else 0.0

    def copy(self) -> "_AdxRegimeEngine":
        other = _AdxRegimeEngine.__new__(_AdxRegimeEngine)
        other.length = self.length
        other.tr_ema = self.tr_ema.copy()
        other.plus_ema = self.plus_ema.copy()
        other.minus_ema = self.minus_ema.copy()
        other.dx_ema = self.dx_ema.copy()
        other.prev_h = self.prev_h
        other.prev_l = self.prev_l
        other.prev_c = self.prev_c
        other.n = self.n
        other.last = self.last
        return other


def _make_htf_engine(kind: str):
    if kind == "trend":
        return _TrendEngine()
    if kind == "rsi":
        return _RsiEngine()
    if kind == "atr_ratio":
        return _AtrRatioEngine()
    if kind == "regime":
        return _AdxRegimeEngine()
    return _TrendEngine()


def _resample_htf_bars(rows: list[dict], target_secs: int) -> list[dict]:
    """Completed HTF buckets plus the current forming bucket (no look-ahead)."""
    completed: list[dict] = []
    cur: dict | None = None
    cur_bucket: int | None = None
    for bar in rows:
        if not isinstance(bar, dict):
            continue
        t = _bar_unix(bar)
        fields = _fields(bar)
        if t is None or fields is None:
            continue
        bucket = int(t // target_secs) * target_secs
        if cur is None:
            cur_bucket = bucket
            cur = {"time": bucket, **fields}
        elif bucket == cur_bucket:
            cur["high"] = max(cur["high"], fields["high"])
            cur["low"] = min(cur["low"], fields["low"])
            cur["close"] = fields["close"]
            cur["volume"] += fields["volume"]
        else:
            completed.append(cur)
            cur_bucket = bucket
            cur = {"time": bucket, **fields}
    if cur is not None:
        completed.append(cur)
    return completed


def _causal_htf_series(
    rows: list[dict],
    target_secs: int,
    kind: str,
    *,
    default: float,
) -> np.ndarray:
    """Walk source bars left→right; forming HTF bucket never sees future bars.

    Incremental O(n): committed HTF bars update indicator state once; the
    forming bucket is applied on a copy so intra-bucket ticks stay causal.
    """
    n = len(rows)
    out = np.full(n, default, dtype=np.float64)
    if n == 0 or target_secs <= 0:
        return out

    engine = _make_htf_engine(kind)
    cur: dict | None = None
    cur_bucket: int | None = None

    for i, bar in enumerate(rows):
        if not isinstance(bar, dict):
            out[i] = out[i - 1] if i else default
            continue
        t = _bar_unix(bar)
        fields = _fields(bar)
        if t is None or fields is None:
            out[i] = out[i - 1] if i else default
            continue
        bucket = int(t // target_secs) * target_secs
        if cur is None:
            cur_bucket = bucket
            cur = {"time": bucket, **fields}
        elif bucket == cur_bucket:
            cur["high"] = max(cur["high"], fields["high"])
            cur["low"] = min(cur["low"], fields["low"])
            cur["close"] = fields["close"]
            cur["volume"] += fields["volume"]
        else:
            engine.commit(cur)
            cur_bucket = bucket
            cur = {"time": bucket, **fields}
        tmp = engine.copy()
        tmp.commit(cur)
        out[i] = tmp.last
    return out


def compute_htf_feature_matrix(
    candles: list[dict] | Any,
    *,
    timeframe: str | None = None,
) -> dict[str, np.ndarray]:
    """Causal HTF features aligned to every source bar (no look-ahead)."""
    if hasattr(candles, "to_dict"):
        rows = candles.to_dict("records")
    else:
        rows = list(candles or [])
    n = len(rows)
    zeros = {name: np.zeros(n, dtype=np.float64) for name in HTF_FEATURE_NAMES}
    if n == 0:
        return zeros

    source_secs = _infer_source_secs(rows, timeframe)

    def _target(secs: int) -> int:
        return secs if secs > source_secs else source_secs

    zeros["htf_trend_1h"] = _causal_htf_series(rows, _target(3600), "trend", default=0.0)
    zeros["htf_rsi_1h"] = _causal_htf_series(rows, _target(3600), "rsi", default=0.5)
    zeros["htf_atr_ratio_4h"] = _causal_htf_series(rows, _target(14400), "atr_ratio", default=0.0)
    zeros["htf_regime_daily"] = _causal_htf_series(rows, _target(86400), "regime", default=0.0)
    return zeros


def compute_htf_features_for_bar(
    current: dict,
    history_rows: list[dict] | None = None,
    *,
    timeframe: str | None = None,
) -> dict[str, float]:
    """HTF features for a single bar from ``history + current`` (resample parity).

    Only the last causal HTF snapshot is needed, so this resamples once and
    scores the HTF series once per kind — not a full per-source-bar matrix.
    """
    rows = list(history_rows or [])
    rows.append(dict(current))
    if not rows:
        return {name: 0.0 for name in HTF_FEATURE_NAMES}
    source_secs = _infer_source_secs(rows, timeframe)

    def _target(secs: int) -> int:
        return secs if secs > source_secs else source_secs

    return {
        "htf_trend_1h": float(
            _htf_indicator_snapshot(_resample_htf_bars(rows, _target(3600)), "trend")
        ),
        "htf_rsi_1h": float(
            _htf_indicator_snapshot(_resample_htf_bars(rows, _target(3600)), "rsi")
        ),
        "htf_atr_ratio_4h": float(
            _htf_indicator_snapshot(_resample_htf_bars(rows, _target(14400)), "atr_ratio")
        ),
        "htf_regime_daily": float(
            _htf_indicator_snapshot(_resample_htf_bars(rows, _target(86400)), "regime")
        ),
    }
