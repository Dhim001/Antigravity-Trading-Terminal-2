"""Numba-accelerated ML feature kernels for backtest / train matrices.

Live ``evaluate()`` stays on the pure-Python path. These kernels must match
``CVDTracker`` / ``VPINTracker`` / rolling helpers used by
``compute_signal_feature_matrix_vectorized`` (evaluate lookback parity).

Falls back to pure NumPy/Python if Numba is unavailable.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_NUMBA_READY: bool | None = None
_jit_micro = None
_jit_roll_std = None
_jit_streaks = None
_jit_roll_mean = None


def numba_features_enabled() -> bool:
    return os.environ.get("BACKTEST_NUMBA_FEATURES", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _ensure_numba() -> bool:
    global _NUMBA_READY, _jit_micro, _jit_roll_std, _jit_streaks, _jit_roll_mean
    if _NUMBA_READY is not None:
        return _NUMBA_READY
    if not numba_features_enabled():
        _NUMBA_READY = False
        return False
    try:
        from numba import njit

        @njit(cache=True)
        def _roll_mean_prior(x, window):
            n = x.shape[0]
            out = np.zeros(n, dtype=np.float64)
            if n == 0 or window <= 0:
                return out
            csum = np.empty(n + 1, dtype=np.float64)
            csum[0] = 0.0
            for i in range(n):
                csum[i + 1] = csum[i] + x[i]
            for i in range(n):
                start = i - window
                if start < 0:
                    start = 0
                count = i - start
                if count > 0:
                    out[i] = (csum[i] - csum[start]) / count
            return out

        @njit(cache=True)
        def _roll_std_prior(x, window):
            n = x.shape[0]
            out = np.zeros(n, dtype=np.float64)
            if n == 0 or window <= 0:
                return out
            for i in range(n):
                start = i - window
                if start < 0:
                    start = 0
                count = i - start
                if count < 1:
                    continue
                mean = 0.0
                for k in range(start, i):
                    mean += x[k]
                mean /= count
                var = 0.0
                for k in range(start, i):
                    d = x[k] - mean
                    var += d * d
                var /= count
                out[i] = np.sqrt(var) if var > 0.0 else 0.0
            return out

        @njit(cache=True)
        def _consecutive_streaks(close, flb):
            n = close.shape[0]
            up = np.zeros(n, dtype=np.float64)
            down = np.zeros(n, dtype=np.float64)
            for j in range(n):
                start = j - flb
                if start < 0:
                    start = 0
                count = 0
                i = j
                while i > start:
                    if close[i] > close[i - 1]:
                        if count >= 0:
                            count += 1
                        else:
                            break
                    elif close[i] < close[i - 1]:
                        if count <= 0:
                            count -= 1
                        else:
                            break
                    else:
                        break
                    i -= 1
                if count > 0:
                    up[j] = count / 10.0
                elif count < 0:
                    down[j] = (-count) / 10.0
            return up, down

        @njit(cache=True)
        def _classify_bs(o, c, h, l, vol):
            if vol <= 0.0:
                return 0.0, 0.0
            rng = h - l
            if rng < 1e-9:
                rng = 1e-9
            body = c - o
            buy_share = 0.5 + 0.5 * (body / rng)
            if buy_share < 0.0:
                buy_share = 0.0
            if buy_share > 1.0:
                buy_share = 1.0
            return vol * buy_share, vol * (1.0 - buy_share)

        @njit(cache=True)
        def _cvd_feats_from_deltas(deltas, n_d):
            if n_d < 2:
                return 0.0, 0.0
            mean = 0.0
            for i in range(n_d):
                mean += deltas[i]
            mean /= n_d
            var = 0.0
            for i in range(n_d):
                d = deltas[i] - mean
                var += d * d
            var /= n_d
            std = np.sqrt(var) if var > 0.0 else 0.0
            z = 0.0
            if std > 1e-9:
                z = (deltas[n_d - 1] - mean) / std
            x_mean = (n_d - 1) * 0.5
            y_mean = mean
            num = 0.0
            den = 0.0
            for i in range(n_d):
                dx = i - x_mean
                num += dx * (deltas[i] - y_mean)
                den += dx * dx
            slope = num / den if den > 0.0 else 0.0
            return z, slope

        @njit(cache=True)
        def _microstructure_windowed(open_, close, high, low, volume, flb):
            n = close.shape[0]
            cvd_z = np.zeros(n, dtype=np.float64)
            cvd_slope = np.zeros(n, dtype=np.float64)
            vpin = np.zeros(n, dtype=np.float64)
            # Max window length: flb priors + current
            max_w = flb + 1
            if max_w < 1:
                max_w = 1
            deltas = np.empty(max_w + 5, dtype=np.float64)
            # VPIN state buffers
            imb_buf = np.empty(50, dtype=np.float64)

            for j in range(n):
                if j == 0:
                    continue
                start = j - flb
                if start < 0:
                    start = 0
                lookback = j - start
                if lookback < 20:
                    lookback = 20

                # --- CVD over bars [start, j] ---
                n_d = 0
                for i in range(start, j + 1):
                    b, s = _classify_bs(open_[i], close[i], high[i], low[i], volume[i])
                    dlt = b - s
                    if n_d < lookback:
                        deltas[n_d] = dlt
                        n_d += 1
                    else:
                        # deque maxlen: drop oldest
                        for k in range(lookback - 1):
                            deltas[k] = deltas[k + 1]
                        deltas[lookback - 1] = dlt
                        n_d = lookback
                z, slope = _cvd_feats_from_deltas(deltas, n_d)
                cvd_z[j] = z
                cvd_slope[j] = slope

                # --- VPIN over bars [start, j] ---
                n_buckets = 50
                bucket_vol = 0.0
                cur_vol = 0.0
                cur_buy = 0.0
                cur_sell = 0.0
                n_imb = 0
                ema = 0.0
                alpha = 2.0 / (n_buckets + 1.0)
                first_ingest = True

                for i in range(start, j + 1):
                    b, s = _classify_bs(open_[i], close[i], high[i], low[i], volume[i])
                    vol = b + s
                    if vol <= 0.0:
                        continue
                    if first_ingest and bucket_vol <= 0.0 and cur_vol == 0.0 and n_imb == 0:
                        bucket_vol = vol
                        first_ingest = False
                    target = bucket_vol if bucket_vol > 0.0 else vol
                    rb = b
                    rs = s
                    while rb + rs > 0.0:
                        space = target - cur_vol
                        if space <= 0.0:
                            # close bucket
                            if cur_vol > 0.0:
                                imb = abs(cur_buy - cur_sell) / cur_vol
                                if n_imb < n_buckets:
                                    imb_buf[n_imb] = imb
                                    n_imb += 1
                                else:
                                    for k in range(n_buckets - 1):
                                        imb_buf[k] = imb_buf[k + 1]
                                    imb_buf[n_buckets - 1] = imb
                                if ema == 0.0 and n_imb == 1:
                                    ema = imb
                                else:
                                    ema = (1.0 - alpha) * ema + alpha * imb
                                cur_vol = 0.0
                                cur_buy = 0.0
                                cur_sell = 0.0
                            continue
                        take = rb + rs
                        if take > space:
                            take = space
                        total = rb + rs
                        b_take = take * (rb / total) if total > 0.0 else 0.0
                        s_take = take * (rs / total) if total > 0.0 else 0.0
                        cur_vol += take
                        cur_buy += b_take
                        cur_sell += s_take
                        rb -= b_take
                        rs -= s_take
                        if cur_vol >= target - 1e-9:
                            imb = abs(cur_buy - cur_sell) / cur_vol if cur_vol > 0.0 else 0.0
                            if n_imb < n_buckets:
                                imb_buf[n_imb] = imb
                                n_imb += 1
                            else:
                                for k in range(n_buckets - 1):
                                    imb_buf[k] = imb_buf[k + 1]
                                imb_buf[n_buckets - 1] = imb
                            if ema == 0.0 and n_imb == 1:
                                ema = imb
                            else:
                                ema = (1.0 - alpha) * ema + alpha * imb
                            cur_vol = 0.0
                            cur_buy = 0.0
                            cur_sell = 0.0

                if n_imb == 0:
                    vpin[j] = 0.0
                else:
                    v = ema
                    if v < 0.0:
                        v = 0.0
                    if v > 1.0:
                        v = 1.0
                    vpin[j] = v

            return cvd_z, cvd_slope, vpin

        # Warm up compile once
        _z = np.zeros(3, dtype=np.float64)
        _roll_mean_prior(_z, 2)
        _roll_std_prior(_z, 2)
        _consecutive_streaks(_z, 2)
        _microstructure_windowed(_z, _z, _z, _z, _z, 2)

        _jit_roll_mean = _roll_mean_prior
        _jit_roll_std = _roll_std_prior
        _jit_streaks = _consecutive_streaks
        _jit_micro = _microstructure_windowed
        _NUMBA_READY = True
        logger.info("Numba ML feature kernels ready")
        return True
    except Exception as exc:
        logger.warning("Numba ML feature kernels unavailable (%s) — Python fallback", exc)
        _NUMBA_READY = False
        return False


def _py_roll_mean_prior(x: np.ndarray, window: int) -> np.ndarray:
    n = len(x)
    out = np.zeros(n, dtype=np.float64)
    if n == 0 or window <= 0:
        return out
    csum = np.concatenate([[0.0], np.cumsum(x)])
    for i in range(n):
        start = max(0, i - window)
        count = i - start
        if count > 0:
            out[i] = (csum[i] - csum[start]) / count
    return out


def _py_roll_std_prior(x: np.ndarray, window: int) -> np.ndarray:
    n = len(x)
    out = np.zeros(n, dtype=np.float64)
    if n == 0 or window <= 0:
        return out
    for i in range(n):
        start = max(0, i - window)
        if i - start < 1:
            continue
        seg = x[start:i]
        out[i] = float(np.std(seg)) if len(seg) else 0.0
    return out


def _py_consecutive_streaks(close: np.ndarray, flb: int) -> tuple[np.ndarray, np.ndarray]:
    n = len(close)
    up = np.zeros(n, dtype=np.float64)
    down = np.zeros(n, dtype=np.float64)
    for j in range(n):
        start = max(0, j - flb)
        count = 0
        for i in range(j, start, -1):
            if close[i] > close[i - 1]:
                if count >= 0:
                    count += 1
                else:
                    break
            elif close[i] < close[i - 1]:
                if count <= 0:
                    count -= 1
                else:
                    break
            else:
                break
        up[j] = max(0.0, float(count)) / 10.0
        down[j] = max(0.0, float(-count)) / 10.0
    return up, down


def rolling_mean_prior_fast(x: np.ndarray, window: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if _ensure_numba() and _jit_roll_mean is not None:
        return _jit_roll_mean(x, int(window))
    return _py_roll_mean_prior(x, window)


def rolling_std_prior_fast(x: np.ndarray, window: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if _ensure_numba() and _jit_roll_std is not None:
        return _jit_roll_std(x, int(window))
    return _py_roll_std_prior(x, window)


def consecutive_streaks_fast(close: np.ndarray, flb: int) -> tuple[np.ndarray, np.ndarray]:
    close = np.asarray(close, dtype=np.float64)
    if _ensure_numba() and _jit_streaks is not None:
        return _jit_streaks(close, int(flb))
    return _py_consecutive_streaks(close, flb)


def microstructure_windowed_fast(
    open_: np.ndarray,
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    flb: int,
    *,
    cancel_cb: Any | None = None,
    progress_cb: Any | None = None,
    progress_base: int = 0,
    progress_total: int | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Numba windowed CVD/VPIN when available; else Python tracker path."""
    open_ = np.asarray(open_, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    flb = int(flb)

    if _ensure_numba() and _jit_micro is not None:
        n = close.shape[0]
        need_chunks = cancel_cb is not None or progress_cb is not None or n >= 4000
        if not need_chunks:
            return _jit_micro(open_, close, high, low, volume, flb)
        # Chunked numba: cooperative cancel + UI stall fingerprint progress.
        cvd_z = np.zeros(n, dtype=np.float64)
        cvd_slope = np.zeros(n, dtype=np.float64)
        vpin = np.zeros(n, dtype=np.float64)
        chunk = max(1024, n // 20)
        tot = max(1, int(progress_total if progress_total is not None else n))
        for start in range(0, n, chunk):
            if cancel_cb is not None and cancel_cb():
                raise InterruptedError("ml_cancel_requested")
            end = min(start + chunk, n)
            # Need flb prior bars before ``start`` for window parity.
            pad = max(0, start - flb)
            z, s, v = _jit_micro(
                open_[pad:end], close[pad:end], high[pad:end],
                low[pad:end], volume[pad:end], flb,
            )
            off = start - pad
            cvd_z[start:end] = z[off:]
            cvd_slope[start:end] = s[off:]
            vpin[start:end] = v[off:]
            if progress_cb is not None:
                span = max(0, tot - progress_base)
                done = progress_base + int((end / max(n, 1)) * span)
                try:
                    progress_cb(min(done, tot), tot)
                except Exception:
                    pass
        return cvd_z, cvd_slope, vpin

    # Lazy import avoids circular init with ml_feature_engineering.
    from app.services.bots import ml_feature_engineering as mfe

    return mfe._microstructure_windowed_python(
        open_, close, high, low, volume, flb,
        cancel_cb=cancel_cb,
        progress_cb=progress_cb,
        progress_base=progress_base,
        progress_total=progress_total,
    )
