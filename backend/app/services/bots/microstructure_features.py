"""Microstructure features — CVD + VPIN.

Phase 3.7 of the Signal Enhancement Plan.

Two order-flow features that the existing microstructure strategies and the
ML feature pipeline can both consume:

1. **CVD (Cumulative Volume Delta)** — running sum of (buy_volume − sell_volume).
   When per-trade tick data is available, buy/sell are classified by the
   Lee-Ready rule (trade price vs midpoint). When only OHLCV is available, we
   fall back to a bar-proxy: split bar volume by the candle body direction.

2. **VPIN (Volume-synchronized Probability of Informed Trading)** —
   Easley, López de Prado, O'Hara (2012). Bucket trades by equal volume
   buckets (not time), compute |buy_vol − sell_vol| / bucket_vol per bucket,
   then EMA over the last N buckets. High VPIN → informed flow → toxicity.

Both are designed to be computed incrementally (streaming) and to degrade
gracefully when only OHLCV is available — the common case in this app.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Sequence

DEFAULT_VPIN_BUCKETS = 50
DEFAULT_VPIN_BUCKET_VOL = 0.0  # 0 = auto-size buckets to bar volume
DEFAULT_CVD_LOOKBACK = 200


@dataclass
class _Bucket:
    vol: float = 0.0
    buy: float = 0.0
    sell: float = 0.0


@dataclass
class _BuySell:
    buy: float
    sell: float


def classify_bar_buy_sell(
    *,
    open_: float,
    close: float,
    high: float,
    low: float,
    volume: float,
) -> _BuySell:
    """Bar-proxy buy/sell split when no tick data is available.

    Uses the candle body direction to bias the split: a bullish bar (close >
    open) attributes more volume to buyers, a bearish bar to sellers. The
    split is proportional to the body's share of the range, so a doji
    (no body) splits volume 50/50.
    """
    if volume <= 0:
        return _BuySell(0.0, 0.0)
    rng = max(high - low, 1e-9)
    body = close - open_
    # buy_share ∈ [0, 1]: 0.5 for doji, →1 for strong bull, →0 for strong bear
    buy_share = 0.5 + 0.5 * (body / rng)
    buy_share = max(0.0, min(1.0, buy_share))
    return _BuySell(buy=volume * buy_share, sell=volume * (1.0 - buy_share))


def classify_tick_buy_sell(
    price: float,
    bid: float,
    ask: float,
    size: float,
) -> _BuySell:
    """Lee-Ready tick classification: above midpoint → buy, below → sell."""
    if size <= 0:
        return _BuySell(0.0, 0.0)
    mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else price
    if price >= mid:
        return _BuySell(buy=size, sell=0.0)
    return _BuySell(buy=0.0, sell=size)


# ── CVD ────────────────────────────────────────────────────────────────────


class CVDTracker:
    """Incremental CVD with a rolling lookback window.

    The raw CVD is a running sum; for features we expose both the raw value
    and a rolling z-score (normalised) so ML models can use it without
    worrying about the absolute drift.
    """

    def __init__(self, lookback: int = DEFAULT_CVD_LOOKBACK):
        self.lookback = max(1, int(lookback))
        self._deltas: deque[float] = deque(maxlen=self.lookback)
        self._cvd: float = 0.0

    def update(self, buy: float, sell: float) -> float:
        delta = float(buy) - float(sell)
        self._deltas.append(delta)
        self._cvd += delta
        return self._cvd

    def update_bar(
        self, *, open_: float, close: float, high: float, low: float, volume: float
    ) -> float:
        bs = classify_bar_buy_sell(
            open_=open_, close=close, high=high, low=low, volume=volume,
        )
        return self.update(bs.buy, bs.sell)

    @property
    def cvd(self) -> float:
        return self._cvd

    @property
    def cvd_z(self) -> float:
        """Rolling z-score of per-bar deltas — the normalised feature."""
        n = len(self._deltas)
        if n < 2:
            return 0.0
        arr = list(self._deltas)
        mean = sum(arr) / n
        var = sum((x - mean) ** 2 for x in arr) / n
        std = math.sqrt(var)
        if std <= 1e-9:
            return 0.0
        latest = arr[-1]
        return (latest - mean) / std

    @property
    def cvd_slope(self) -> float:
        """Slope of CVD over the lookback (linear fit on the deltas)."""
        n = len(self._deltas)
        if n < 2:
            return 0.0
        arr = list(self._deltas)
        xs = list(range(n))
        x_mean = sum(xs) / n
        y_mean = sum(arr) / n
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, arr))
        den = sum((x - x_mean) ** 2 for x in xs)
        return num / den if den > 0 else 0.0


# ── VPIN ───────────────────────────────────────────────────────────────────


class VPINTracker:
    """Volume-synchronized VPIN (Easley-López de Prado-O'Hara 2012).

    Trades are bucketed by cumulative volume (not time). Each bucket records
    buy/sell volume; when a bucket fills, its imbalance |buy−sell|/vol is
    pushed into a rolling EMA. The current VPIN is the EMA of the last
    ``n_buckets`` bucket imbalances.

    When only OHLCV bars are available, each bar is treated as one "trade"
    contributing its full volume to the current bucket, with buy/sell split
    via the bar proxy.
    """

    def __init__(
        self,
        *,
        n_buckets: int = DEFAULT_VPIN_BUCKETS,
        bucket_vol: float = DEFAULT_VPIN_BUCKET_VOL,
    ):
        self.n_buckets = max(1, int(n_buckets))
        self.bucket_vol = float(bucket_vol)
        self._current = _Bucket()
        self._bucket_imbalances: deque[float] = deque(maxlen=self.n_buckets)
        self._ema: float = 0.0
        self._alpha: float = 2.0 / (self.n_buckets + 1)

    def _ingest(self, buy: float, sell: float) -> None:
        vol = float(buy) + float(sell)
        if vol <= 0:
            return
        # Auto-size bucket volume on first ingestion if unset.
        if self.bucket_vol <= 0 and self._current.vol == 0 and not self._bucket_imbalances:
            self.bucket_vol = vol  # first bar's volume → one bucket per bar
        target = self.bucket_vol if self.bucket_vol > 0 else vol
        remaining_buy, remaining_sell = buy, sell
        while remaining_buy + remaining_sell > 0:
            space = max(0.0, target - self._current.vol)
            if space <= 0:
                self._close_bucket()
                continue
            take = min(remaining_buy + remaining_sell, space)
            # Proportional split of the take between buy and sell
            total = remaining_buy + remaining_sell
            b_take = take * (remaining_buy / total) if total > 0 else 0.0
            s_take = take * (remaining_sell / total) if total > 0 else 0.0
            self._current.vol += take
            self._current.buy += b_take
            self._current.sell += s_take
            remaining_buy -= b_take
            remaining_sell -= s_take
            if self._current.vol >= target - 1e-9:
                self._close_bucket()

    def _close_bucket(self) -> None:
        if self._current.vol <= 0:
            return
        imb = abs(self._current.buy - self._current.sell) / self._current.vol
        self._bucket_imbalances.append(imb)
        # EMA update
        if self._ema == 0.0 and len(self._bucket_imbalances) == 1:
            self._ema = imb
        else:
            self._ema = (1.0 - self._alpha) * self._ema + self._alpha * imb
        self._current = _Bucket()

    def update(self, buy: float, sell: float) -> float:
        self._ingest(buy, sell)
        return self.vpin

    def update_bar(
        self, *, open_: float, close: float, high: float, low: float, volume: float
    ) -> float:
        bs = classify_bar_buy_sell(
            open_=open_, close=close, high=high, low=low, volume=volume,
        )
        return self.update(bs.buy, bs.sell)

    @property
    def vpin(self) -> float:
        """Current VPIN ∈ [0, 1]."""
        if not self._bucket_imbalances:
            return 0.0
        return max(0.0, min(1.0, self._ema))

    @property
    def n_filled_buckets(self) -> int:
        return len(self._bucket_imbalances)


# ── Batch helpers (for backtest / training feature build) ─────────────────


def compute_cvd_series(candles: Sequence[dict]) -> list[float]:
    """Batch CVD over a candle series — returns one CVD value per bar."""
    tracker = CVDTracker(lookback=len(candles))
    out: list[float] = []
    for c in candles:
        tracker.update_bar(
            open_=float(c.get("open", 0)),
            close=float(c.get("close", 0)),
            high=float(c.get("high", 0)),
            low=float(c.get("low", 0)),
            volume=float(c.get("volume", 0)),
        )
        out.append(tracker.cvd)
    return out


def compute_vpin_series(
    candles: Sequence[dict],
    *,
    n_buckets: int = DEFAULT_VPIN_BUCKETS,
) -> list[float]:
    """Batch VPIN over a candle series — returns one VPIN value per bar."""
    tracker = VPINTracker(n_buckets=n_buckets)
    out: list[float] = []
    for c in candles:
        tracker.update_bar(
            open_=float(c.get("open", 0)),
            close=float(c.get("close", 0)),
            high=float(c.get("high", 0)),
            low=float(c.get("low", 0)),
            volume=float(c.get("volume", 0)),
        )
        out.append(tracker.vpin)
    return out
