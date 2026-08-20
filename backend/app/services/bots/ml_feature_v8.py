"""Schema v8 feature families: ICT, events, OFI, volume profile, AVWAP, YZ, FFD.

Append-only columns. Existing v7 names keep their meaning.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

import numpy as np

from app.services.bots.ml_feature_advanced import frac_diff_last, frac_diff_series
from app.services.bots.ml_feature_ict import ICT_FEATURE_NAMES, ict_feature_matrix

logger = logging.getLogger(__name__)

EVENT_FEATURE_NAMES: tuple[str, ...] = (
    "hours_to_earnings",
    "earnings_flag",
    "macro_impact_max",
    "sentiment_available",
)

OFI_FEATURE_NAMES: tuple[str, ...] = (
    "ofi_bair",
    "ofi_mlofi",
    "book_available",
)

PROFILE_FEATURE_NAMES: tuple[str, ...] = (
    "poc_dist_atr",
    "vah_dist_atr",
    "val_dist_atr",
    "in_value_area",
)

HYGIENE_FEATURE_NAMES: tuple[str, ...] = (
    "avwap_session_dev",
    "rv_yang_zhang_20",
    "overnight_gap",
    "frac_diff_close_ffd",
)

V8_FEATURE_NAMES: tuple[str, ...] = (
    ICT_FEATURE_NAMES
    + EVENT_FEATURE_NAMES
    + OFI_FEATURE_NAMES
    + PROFILE_FEATURE_NAMES
    + HYGIENE_FEATURE_NAMES
)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    return v


def _bar_unix(bar: dict | None) -> float | None:
    if not isinstance(bar, dict):
        return None
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


def _row_at(candles: Any, i: int) -> Any:
    if hasattr(candles, "iloc"):
        try:
            return candles.iloc[i]
        except Exception:
            return None
    try:
        return candles[i]
    except Exception:
        return None


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _utc_day_id(ts: float | None) -> int:
    if ts is None or not math.isfinite(ts):
        return 0
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return dt.year * 1000 + dt.timetuple().tm_yday
    except (OSError, OverflowError, ValueError):
        return int(float(ts) // 86400.0)


def _is_crypto(symbol: str | None) -> bool:
    try:
        from app.services.altdata.calendar import is_crypto_symbol

        if not symbol:
            return True
        return bool(is_crypto_symbol(symbol))
    except Exception:
        return True


def yang_zhang_vol(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    window: int = 20,
) -> np.ndarray:
    n = len(close)
    out = np.zeros(n, dtype=np.float64)
    if n == 0 or window < 2:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        log_o = np.log(np.maximum(open_, 1e-12))
        log_c = np.log(np.maximum(close, 1e-12))
        log_h = np.log(np.maximum(high, 1e-12))
        log_l = np.log(np.maximum(low, 1e-12))
    overnight = np.zeros(n, dtype=np.float64)
    overnight[1:] = log_o[1:] - log_c[:-1]
    oc = log_c - log_o
    rs = (log_h - log_c) * (log_h - log_o) + (log_l - log_c) * (log_l - log_o)
    k = 0.34 / (1.34 + (window + 1) / max(window - 1, 1))
    for i in range(window, n):
        sl = slice(i - window + 1, i + 1)
        var_o = float(np.var(overnight[sl]))
        var_c = float(np.var(oc[sl]))
        var_rs = float(np.mean(rs[sl]))
        out[i] = math.sqrt(max(0.0, var_o + k * var_c + (1.0 - k) * var_rs))
    return out


def overnight_gap_series(
    open_: np.ndarray,
    close: np.ndarray,
    *,
    crypto: bool = False,
) -> np.ndarray:
    n = len(close)
    out = np.zeros(n, dtype=np.float64)
    if crypto or n < 2:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        prev = np.maximum(close[:-1], 1e-12)
        out[1:] = (open_[1:] - close[:-1]) / prev
    np.nan_to_num(out, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    return out


def _adf_tstat(series: np.ndarray) -> float:
    x = np.asarray(series, dtype=np.float64)
    x = x[np.isfinite(x)]
    if len(x) < 20:
        return 0.0
    lag = x[:-1]
    dy = np.diff(x)
    n = len(dy)
    design = np.column_stack([np.ones(n), lag])
    try:
        beta, _, _, _ = np.linalg.lstsq(design, dy, rcond=None)
    except np.linalg.LinAlgError:
        return 0.0
    resid = dy - design @ beta
    dof = max(n - 2, 1)
    sigma2 = float(np.dot(resid, resid)) / dof
    try:
        xtx_inv = np.linalg.pinv(design.T @ design)
        se_b = math.sqrt(max(sigma2 * float(xtx_inv[1, 1]), 1e-18))
    except np.linalg.LinAlgError:
        return 0.0
    if se_b <= 0:
        return 0.0
    return float(beta[1] / se_b)


def select_ffd_d(close: np.ndarray, *, critical: float = -2.86) -> float:
    """Smallest d in [0.2, 0.6] whose frac-diff series looks ADF-stationary."""
    x = np.asarray(close, dtype=np.float64)
    if len(x) < 30:
        return 0.4
    for d in (0.2, 0.3, 0.4, 0.5, 0.6):
        fd = frac_diff_series(x, d=d)
        nz = np.flatnonzero(np.abs(fd) > 1e-15)
        tail = fd[int(nz[0]):] if len(nz) else fd
        if len(tail) < 20:
            continue
        if _adf_tstat(tail) < critical:
            return float(d)
    return 0.4


_LAST_FFD_D = 0.4


def last_selected_ffd_d() -> float:
    """Last ADF-selected FFD d from the most recent matrix build."""
    return float(_LAST_FFD_D)


def resolve_artifact_ffd_d(meta: dict | None) -> float | None:
    """Read frozen ``frac_diff_d_ffd`` from model metadata / scaler payload."""
    if not isinstance(meta, dict):
        return None
    raw = meta.get("frac_diff_d_ffd")
    if raw is None and isinstance(meta.get("scaler"), dict):
        raw = meta["scaler"].get("frac_diff_d_ffd")
    try:
        d = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(d) or d < 0.1 or d > 0.8:
        return None
    return d


def attach_ffd_d(payload: dict | None = None) -> dict:
    """Copy *payload* and add ``frac_diff_d_ffd`` for scaler / metadata."""
    out = dict(payload or {})
    out["frac_diff_d_ffd"] = last_selected_ffd_d()
    return out


def frac_diff_close_ffd_series(close: np.ndarray, *, d: float | None = None) -> np.ndarray:
    """Prior-only FFD. ADF-selects d every 64 bars unless ``d`` is frozen (serve)."""
    global _LAST_FFD_D
    n = len(close)
    out = np.zeros(n, dtype=np.float64)
    if n == 0:
        return out
    frozen = False
    d_cur = 0.4
    if d is not None:
        try:
            d_cur = float(d)
            frozen = math.isfinite(d_cur) and 0.1 <= d_cur <= 0.8
        except (TypeError, ValueError):
            frozen = False
        if not frozen:
            d_cur = 0.4
    last_sel = -10**9
    for i in range(n):
        if not frozen and i >= 50 and (i - last_sel) >= 64:
            start = max(0, i - 256)
            d_cur = select_ffd_d(close[start:i])
            last_sel = i
        if i < 8:
            continue
        start = max(0, i - 499)
        out[i] = frac_diff_last(close[start : i + 1], d=d_cur)
    np.nan_to_num(out, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    if not frozen:
        _LAST_FFD_D = float(d_cur)
    return out


def volume_profile_features(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    atr: np.ndarray,
    times: np.ndarray | None = None,
    *,
    window: int = 96,
    value_frac: float = 0.70,
) -> dict[str, np.ndarray]:
    n = len(close)
    poc_d = np.zeros(n, dtype=np.float64)
    vah_d = np.zeros(n, dtype=np.float64)
    val_d = np.zeros(n, dtype=np.float64)
    in_va = np.zeros(n, dtype=np.float64)
    if n == 0:
        return {
            "poc_dist_atr": poc_d,
            "vah_dist_atr": vah_d,
            "val_dist_atr": val_d,
            "in_value_area": in_va,
        }

    typical = (high + low + close) / 3.0
    day_ids = None
    if times is not None and len(times) == n:
        day_ids = np.array(
            [_utc_day_id(None if not math.isfinite(float(t)) else float(t)) for t in times],
            dtype=np.int64,
        )

    w = max(20, int(window))
    n_bins = 24
    for i in range(n):
        if day_ids is not None:
            d0 = int(day_ids[i])
            start = i
            while start > 0 and int(day_ids[start - 1]) == d0 and (i - start) < 1500:
                start -= 1
        else:
            start = max(0, i - w + 1)
        if i - start < 8:
            continue
        sl = slice(start, i + 1)
        px = typical[sl]
        vol = np.maximum(volume[sl], 0.0)
        lo = float(np.min(px))
        hi = float(np.max(px))
        if hi - lo < 1e-12:
            continue
        bins = np.clip(
            ((px - lo) / (hi - lo) * (n_bins - 1)).astype(np.int64),
            0,
            n_bins - 1,
        )
        vol_bins = np.zeros(n_bins, dtype=np.float64)
        for b, v in zip(bins, vol):
            vol_bins[int(b)] += float(v)
        total = float(vol_bins.sum())
        if total <= 0:
            continue
        poc_i = int(np.argmax(vol_bins))
        # Expand value area from POC until ``value_frac`` of volume.
        lo_i = hi_i = poc_i
        covered = float(vol_bins[poc_i])
        while covered / total < value_frac and (lo_i > 0 or hi_i < n_bins - 1):
            left = vol_bins[lo_i - 1] if lo_i > 0 else -1.0
            right = vol_bins[hi_i + 1] if hi_i < n_bins - 1 else -1.0
            if right >= left and hi_i < n_bins - 1:
                hi_i += 1
                covered += float(vol_bins[hi_i])
            elif lo_i > 0:
                lo_i -= 1
                covered += float(vol_bins[lo_i])
            else:
                break
        def _bin_price(idx: int) -> float:
            return lo + (hi - lo) * ((idx + 0.5) / n_bins)

        poc = _bin_price(poc_i)
        vah = _bin_price(hi_i)
        val = _bin_price(lo_i)
        atr_i = float(atr[i]) if atr[i] > 1e-12 else 0.0
        c = float(close[i])
        if atr_i > 1e-12:
            poc_d[i] = (c - poc) / atr_i
            vah_d[i] = (c - vah) / atr_i
            val_d[i] = (c - val) / atr_i
        in_va[i] = 1.0 if val <= c <= vah else 0.0
    return {
        "poc_dist_atr": poc_d,
        "vah_dist_atr": vah_d,
        "val_dist_atr": val_d,
        "in_value_area": in_va,
    }


def avwap_session_dev_series(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    atr: np.ndarray,
    times: np.ndarray | None = None,
) -> np.ndarray:
    n = len(close)
    out = np.zeros(n, dtype=np.float64)
    if n == 0:
        return out
    typical = (high + low + close) / 3.0
    day_ids = np.zeros(n, dtype=np.int64)
    if times is not None and len(times) == n:
        for i, t in enumerate(times):
            try:
                tv = float(t)
            except (TypeError, ValueError):
                tv = float("nan")
            day_ids[i] = _utc_day_id(None if not math.isfinite(tv) else tv)
    else:
        # Rolling 24h-equivalent window when timestamps are missing.
        day_ids[:] = np.arange(n) // 96

    cum_pv = 0.0
    cum_v = 0.0
    prev_day = None
    for i in range(n):
        d = int(day_ids[i])
        if prev_day is None or d != prev_day:
            cum_pv = 0.0
            cum_v = 0.0
            prev_day = d
        v = max(0.0, float(volume[i]))
        cum_pv += float(typical[i]) * v
        cum_v += v
        if cum_v <= 1e-12:
            continue
        avwap = cum_pv / cum_v
        atr_i = float(atr[i]) if atr[i] > 1e-12 else 0.0
        if atr_i > 1e-12:
            out[i] = (float(close[i]) - avwap) / atr_i
    np.nan_to_num(out, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    return out


def ofi_feature_matrix(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    candles: Any = None,
) -> dict[str, np.ndarray]:
    from app.services.bots.microstructure_features import classify_bar_buy_sell
    from app.services.bots.strategies_microstructure import compute_bair_mlofi

    n = len(close)
    bair = np.zeros(n, dtype=np.float64)
    mlofi = np.zeros(n, dtype=np.float64)
    book = np.zeros(n, dtype=np.float64)
    proxy = np.zeros(n, dtype=np.float64)
    for i in range(n):
        ob = None
        if candles is not None:
            row = _row_at(candles, i)
            ob = _row_get(row, "_orderbook")
        real_b, real_m = compute_bair_mlofi(ob if isinstance(ob, dict) else None)
        if real_b is not None and real_m is not None:
            bair[i] = float(real_b)
            mlofi[i] = float(real_m)
            book[i] = 1.0
        else:
            bs = classify_bar_buy_sell(
                open_=float(open_[i]),
                close=float(close[i]),
                high=float(high[i]),
                low=float(low[i]),
                volume=float(volume[i]),
            )
            tot = bs.buy + bs.sell
            proxy[i] = ((bs.buy - bs.sell) / tot) if tot > 0 else 0.0
            bair[i] = proxy[i]
    # 3-bar mean of the candle proxy as MLOFI stand-in (ORDERFLOW strategy).
    for i in range(n):
        if book[i] >= 1.0:
            continue
        start = max(0, i - 2)
        mlofi[i] = float(np.mean(proxy[start : i + 1]))
    np.clip(bair, -1.0, 1.0, out=bair)
    np.clip(mlofi, -1.0, 1.0, out=mlofi)
    return {
        "ofi_bair": bair,
        "ofi_mlofi": mlofi,
        "book_available": book,
    }


def _impact_score(raw: Any) -> float:
    s = str(raw or "").strip().lower()
    if s in ("3", "high", "h"):
        return 1.0
    if s in ("2", "medium", "med", "m"):
        return 0.6
    if s in ("1", "low", "l"):
        return 0.3
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if v > 1.0:
        return float(min(1.0, v / 3.0))
    return float(min(1.0, max(0.0, v)))


def _is_earnings_event(ev: dict) -> bool:
    blob = f"{ev.get('event_type') or ''} {ev.get('title') or ''}".lower()
    return "earn" in blob or "eps" in blob


def event_feature_matrix(
    candles: Any,
    symbol: str | None,
    n: int,
) -> dict[str, np.ndarray]:
    hours = np.ones(n, dtype=np.float64)
    flag = np.zeros(n, dtype=np.float64)
    macro = np.zeros(n, dtype=np.float64)
    sent_ok = np.zeros(n, dtype=np.float64)
    if n == 0:
        return {
            "hours_to_earnings": hours,
            "earnings_flag": flag,
            "macro_impact_max": macro,
            "sentiment_available": sent_ok,
        }

    cache: dict[int, tuple[float, float, float, float]] = {}
    for i in range(n):
        row = _row_at(candles, i) if candles is not None else None
        ts = _bar_unix(row) if isinstance(row, dict) else None
        if ts is None:
            continue
        key = int(ts // 60.0)
        if key not in cache:
            h_n, fl, mx, av = 1.0, 0.0, 0.0, 0.0
            try:
                from app.services.altdata.store import (
                    get_corporate_events_near,
                    get_economic_events_near,
                    get_sentiment_summary,
                )

                if symbol:
                    corps = get_corporate_events_near(
                        symbol, epoch=float(ts), window_hours=72.0, limit=20,
                    )
                    best_h = 72.0
                    for ev in corps or []:
                        if not _is_earnings_event(ev):
                            continue
                        from app.services.altdata.store import _parse_timestamp_to_epoch

                        ev_t = _parse_timestamp_to_epoch(ev.get("event_date"))
                        if ev_t is None:
                            continue
                        dt_h = (ev_t - ts) / 3600.0
                        if 0 <= dt_h <= 72:
                            best_h = min(best_h, dt_h)
                            fl = 1.0
                        elif -24 <= dt_h < 0:
                            fl = 1.0
                            best_h = min(best_h, 0.0)
                    h_n = float(min(1.0, max(0.0, best_h / 72.0)))
                    summary = get_sentiment_summary(
                        symbol, epoch=float(ts), lookback_hours=24.0,
                    )
                    mentions = int(summary.get("mention_count") or 0)
                    av = 1.0 if mentions > 0 else 0.0
                ecos = get_economic_events_near(epoch=float(ts), window_hours=24.0, limit=20)
                mx = 0.0
                for ev in ecos or []:
                    mx = max(mx, _impact_score(ev.get("impact")))
            except Exception:
                pass
            cache[key] = (h_n, fl, mx, av)
        hours[i], flag[i], macro[i], sent_ok[i] = cache[key]
    return {
        "hours_to_earnings": hours,
        "earnings_flag": flag,
        "macro_impact_max": macro,
        "sentiment_available": sent_ok,
    }


def v8_feature_matrix(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    atr: np.ndarray,
    *,
    times: np.ndarray | None = None,
    candles: Any = None,
    symbol: str | None = None,
    ffd_d: float | None = None,
) -> dict[str, np.ndarray]:
    n = len(close)
    empty = {name: np.zeros(n, dtype=np.float64) for name in V8_FEATURE_NAMES}
    if n == 0:
        return empty

    out: dict[str, np.ndarray] = {}
    out.update(ict_feature_matrix(open_, high, low, close, atr))
    out.update(event_feature_matrix(candles, symbol, n))
    out.update(ofi_feature_matrix(open_, high, low, close, volume, candles))
    out.update(
        volume_profile_features(high, low, close, volume, atr, times)
    )
    out["avwap_session_dev"] = avwap_session_dev_series(
        high, low, close, volume, atr, times,
    )
    out["rv_yang_zhang_20"] = yang_zhang_vol(open_, high, low, close, 20)
    out["overnight_gap"] = overnight_gap_series(
        open_, close, crypto=_is_crypto(symbol),
    )
    out["frac_diff_close_ffd"] = frac_diff_close_ffd_series(close, d=ffd_d)
    for name in V8_FEATURE_NAMES:
        arr = out.get(name)
        if arr is None or len(arr) != n:
            out[name] = np.zeros(n, dtype=np.float64)
        else:
            np.nan_to_num(out[name], copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    return {name: out[name] for name in V8_FEATURE_NAMES}


def v8_features_for_bar(
    current: dict,
    history_rows: list[dict] | None = None,
    *,
    symbol: str | None = None,
    ffd_d: float | None = None,
) -> dict[str, float]:
    rows = list(history_rows or []) + [dict(current)]
    o = np.array([_safe_float(r.get("open")) for r in rows], dtype=np.float64)
    h = np.array([_safe_float(r.get("high")) for r in rows], dtype=np.float64)
    l = np.array([_safe_float(r.get("low")) for r in rows], dtype=np.float64)
    c = np.array([_safe_float(r.get("close")) for r in rows], dtype=np.float64)
    v = np.array([_safe_float(r.get("volume")) for r in rows], dtype=np.float64)
    atr = np.array(
        [
            _safe_float(r.get("ATR_14") or r.get("ATRr_14"))
            for r in rows
        ],
        dtype=np.float64,
    )
    times = np.array(
        [(_bar_unix(r) if _bar_unix(r) is not None else float("nan")) for r in rows],
        dtype=np.float64,
    )
    mat = v8_feature_matrix(
        o, h, l, c, v, atr,
        times=times,
        candles=rows,
        symbol=symbol or str(current.get("_symbol") or current.get("symbol") or "") or None,
        ffd_d=ffd_d,
    )
    return {k: float(arr[-1]) for k, arr in mat.items()}
