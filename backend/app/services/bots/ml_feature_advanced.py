"""Advanced ML features: realized vol, frac-diff, info-theory, structure, peers, altdata."""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

REALIZED_VOL_FEATURE_NAMES: tuple[str, ...] = (
    "rv_parkinson_20",
    "rv_garman_klass_20",
    "vol_regime_ratio",
    "vol_of_vol",
)

SENTIMENT_FEATURE_NAMES: tuple[str, ...] = (
    "sentiment_score_24h",
    "sentiment_momentum",
    "macro_event_proximity",
)

FRACDIFF_FEATURE_NAMES: tuple[str, ...] = (
    "frac_diff_close",
    "frac_diff_volume",
)

INFO_THEORY_FEATURE_NAMES: tuple[str, ...] = (
    "hurst_exponent_50",
    "sample_entropy_20",
    "information_ratio_20",
)

CROSS_ASSET_FEATURE_NAMES: tuple[str, ...] = (
    "peer_returns_avg",
    "peer_divergence",
    "correlation_rolling_20",
)

STRUCTURE_FEATURE_NAMES: tuple[str, ...] = (
    "dist_to_support_norm",
    "dist_to_resistance_norm",
    "range_position",
)

CRYPTO_DERIV_FEATURE_NAMES: tuple[str, ...] = (
    "funding_rate_norm",
    "oi_change_24h_norm",
)

PHASE1_FEATURE_NAMES = REALIZED_VOL_FEATURE_NAMES  # HTF lives in ml_feature_htf
PHASE2_FEATURE_NAMES = (
    SENTIMENT_FEATURE_NAMES + FRACDIFF_FEATURE_NAMES + INFO_THEORY_FEATURE_NAMES
)
PHASE3_FEATURE_NAMES = (
    CROSS_ASSET_FEATURE_NAMES + STRUCTURE_FEATURE_NAMES + CRYPTO_DERIV_FEATURE_NAMES
)

# Hardcoded peer baskets when GNN correlation matrix is unavailable.
_CRYPTO_PEER_BASKET = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT")
_EQUITY_PEER_BASKET = ("SPY", "QQQ", "IWM", "DIA", "AAPL")
_CRYPTO_INDEX = "BTCUSDT"
_EQUITY_INDEX = "SPY"


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
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


# ── Realized volatility ──────────────────────────────────────────────────


def parkinson_vol(high: np.ndarray, low: np.ndarray, window: int = 20) -> np.ndarray:
    n = len(high)
    out = np.zeros(n, dtype=np.float64)
    if n == 0 or window <= 0:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        log_hl = np.log(np.maximum(high, 1e-12) / np.maximum(low, 1e-12))
    log_hl_sq = np.square(log_hl)
    factor = 1.0 / (4.0 * window * math.log(2.0))
    csum = np.concatenate([[0.0], np.cumsum(log_hl_sq)])
    for i in range(n):
        start = max(0, i - window + 1)
        count = i - start + 1
        if count < max(2, window // 2):
            continue
        out[i] = math.sqrt(max(0.0, factor * (csum[i + 1] - csum[start]) * (window / count)))
    return out


def garman_klass_vol(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    window: int = 20,
) -> np.ndarray:
    n = len(close)
    out = np.zeros(n, dtype=np.float64)
    if n == 0 or window <= 0:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        log_hl = np.log(np.maximum(high, 1e-12) / np.maximum(low, 1e-12))
        log_co = np.log(np.maximum(close, 1e-12) / np.maximum(open_, 1e-12))
    gk = 0.5 * np.square(log_hl) - (2.0 * math.log(2.0) - 1.0) * np.square(log_co)
    csum = np.concatenate([[0.0], np.cumsum(gk)])
    for i in range(n):
        start = max(0, i - window + 1)
        count = i - start + 1
        if count < max(2, window // 2):
            continue
        mean_gk = (csum[i + 1] - csum[start]) / count
        out[i] = math.sqrt(max(0.0, mean_gk))
    return out


def realized_vol_feature_matrix(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> dict[str, np.ndarray]:
    n = len(close)
    rv_p = parkinson_vol(high, low, 20)
    rv_gk = garman_klass_vol(open_, high, low, close, 20)
    vol_regime = np.zeros(n, dtype=np.float64)
    vol_of_vol = np.zeros(n, dtype=np.float64)
    for i in range(n):
        start_med = max(0, i - 99)
        seg = rv_p[start_med : i + 1]
        med = float(np.median(seg)) if len(seg) else 0.0
        vol_regime[i] = (rv_p[i] / med) if med > 1e-12 else 0.0
        start_vov = max(0, i - 19)
        seg_v = rv_p[start_vov : i + 1]
        mean_v = float(np.mean(seg_v)) if len(seg_v) else 0.0
        std_v = float(np.std(seg_v)) if len(seg_v) else 0.0
        vol_of_vol[i] = (std_v / mean_v) if mean_v > 1e-12 else 0.0
    return {
        "rv_parkinson_20": rv_p,
        "rv_garman_klass_20": rv_gk,
        "vol_regime_ratio": vol_regime,
        "vol_of_vol": vol_of_vol,
    }


def realized_vol_features_for_bar(
    current: dict,
    history_rows: list[dict] | None = None,
) -> dict[str, float]:
    rows = list(history_rows or []) + [dict(current)]
    o = np.array([_safe_float(r.get("open")) for r in rows], dtype=np.float64)
    h = np.array([_safe_float(r.get("high")) for r in rows], dtype=np.float64)
    l = np.array([_safe_float(r.get("low")) for r in rows], dtype=np.float64)
    c = np.array([_safe_float(r.get("close")) for r in rows], dtype=np.float64)
    mat = realized_vol_feature_matrix(o, h, l, c)
    return {k: float(v[-1]) for k, v in mat.items()}


# ── Fractional differentiation ───────────────────────────────────────────


def frac_diff_weights(d: float, threshold: float = 1e-4, max_size: int = 500) -> np.ndarray:
    weights = [1.0]
    k = 1
    while k < max_size:
        w = -weights[-1] * (d - k + 1) / k
        if abs(w) < threshold:
            break
        weights.append(w)
        k += 1
    return np.asarray(weights[::-1], dtype=np.float64)


def frac_diff_series(series: np.ndarray, d: float = 0.4, threshold: float = 1e-4) -> np.ndarray:
    """Fixed-width window fractional differentiation (de Prado Ch. 5)."""
    n = len(series)
    out = np.zeros(n, dtype=np.float64)
    if n == 0:
        return out
    weights = frac_diff_weights(d, threshold=threshold)
    width = len(weights)
    for i in range(width - 1, n):
        out[i] = float(np.dot(weights, series[i - width + 1 : i + 1]))
    return out


def frac_diff_last(series: np.ndarray, d: float = 0.4, threshold: float = 1e-4) -> float:
    """Frac-diff of the last point, truncating weights when history is short."""
    x = np.asarray(series, dtype=np.float64)
    n = len(x)
    if n == 0:
        return 0.0
    weights = frac_diff_weights(d, threshold=threshold)
    if n < len(weights):
        weights = weights[-n:]
    val = float(np.dot(weights, x[-len(weights):]))
    if not math.isfinite(val):
        return 0.0
    return val


def fracdiff_feature_matrix(
    close: np.ndarray,
    volume: np.ndarray,
) -> dict[str, np.ndarray]:
    return {
        "frac_diff_close": frac_diff_series(close, d=0.4),
        "frac_diff_volume": frac_diff_series(volume, d=0.3),
    }


# ── Information theory ───────────────────────────────────────────────────


def hurst_rs_rolling(returns: np.ndarray, window: int = 50) -> np.ndarray:
    n = len(returns)
    out = np.full(n, 0.5, dtype=np.float64)
    if n < window or window < 8:
        return out
    log_w = math.log(window)
    for i in range(window, n):
        seg = returns[i - window : i]
        mean_seg = float(np.mean(seg))
        deviate = np.cumsum(seg - mean_seg)
        r = float(np.max(deviate) - np.min(deviate))
        s = float(np.std(seg))
        if s > 1e-12 and r > 0:
            out[i] = float(np.clip(math.log(r / s) / log_w, 0.0, 1.0))
    return out


def sample_entropy_rolling(returns: np.ndarray, window: int = 20, m: int = 2) -> np.ndarray:
    """Cheap approximate entropy proxy on returns (normalized)."""
    n = len(returns)
    out = np.zeros(n, dtype=np.float64)
    if n < window:
        return out
    for i in range(window, n):
        seg = returns[i - window : i]
        if len(seg) < m + 2:
            continue
        # Variance of first differences as a fast entropy proxy in [0, ~1].
        d = np.diff(seg)
        std = float(np.std(d))
        out[i] = float(np.clip(std / (abs(float(np.std(seg))) + 1e-8), 0.0, 2.0)) / 2.0
    return out


def information_ratio_rolling(returns: np.ndarray, window: int = 20) -> np.ndarray:
    n = len(returns)
    out = np.zeros(n, dtype=np.float64)
    for i in range(window, n):
        seg = returns[i - window : i]
        std = float(np.std(seg))
        out[i] = float(np.mean(seg) / std) if std > 1e-12 else 0.0
        out[i] = float(np.clip(out[i], -5.0, 5.0))
    return out


def info_theory_feature_matrix(close: np.ndarray) -> dict[str, np.ndarray]:
    n = len(close)
    returns = np.zeros(n, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        prev = np.roll(close, 1)
        prev[0] = close[0]
        returns = np.where(prev > 0, (close - prev) / prev, 0.0)
    return {
        "hurst_exponent_50": hurst_rs_rolling(returns, 50),
        "sample_entropy_20": sample_entropy_rolling(returns, 20),
        "information_ratio_20": information_ratio_rolling(returns, 20),
    }


# ── Market structure ─────────────────────────────────────────────────────


def structure_feature_matrix(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    window: int = 20,
) -> dict[str, np.ndarray]:
    n = len(close)
    dist_sup = np.zeros(n, dtype=np.float64)
    dist_res = np.zeros(n, dtype=np.float64)
    range_pos = np.full(n, 0.5, dtype=np.float64)
    for i in range(n):
        # Prior window only (no look-ahead) — matches ICT_SMC shift(1).
        start = max(0, i - window)
        if i - start < 2:
            continue
        rh = float(np.max(high[start:i]))
        rl = float(np.min(low[start:i]))
        rng = rh - rl
        c = close[i]
        if c > 0:
            dist_sup[i] = (c - rl) / c
            dist_res[i] = (rh - c) / c
        range_pos[i] = ((c - rl) / rng) if rng > 1e-12 else 0.5
    return {
        "dist_to_support_norm": dist_sup,
        "dist_to_resistance_norm": dist_res,
        "range_position": range_pos,
    }


# ── Sentiment / macro (zeros when history missing) ───────────────────────


def sentiment_features_for_epoch(symbol: str | None, bar_time: float | None) -> dict[str, float]:
    """Time-aligned sentiment; missing history → neutral zeros (still shipped)."""
    out = {
        "sentiment_score_24h": 0.0,
        "sentiment_momentum": 0.0,
        "macro_event_proximity": 0.0,
    }
    if not symbol or bar_time is None or not math.isfinite(float(bar_time)):
        return out
    try:
        from app.services.altdata.store import get_sentiment_summary, hours_to_next_macro_event

        summary = get_sentiment_summary(symbol, epoch=float(bar_time), lookback_hours=24.0)
        score = _safe_float(summary.get("aggregate_score"), 0.0)
        prior = get_sentiment_summary(
            symbol, epoch=float(bar_time) - 86400.0, lookback_hours=24.0,
        )
        prior_score = _safe_float(prior.get("aggregate_score"), 0.0)
        out["sentiment_score_24h"] = float(np.clip(score, -1.0, 1.0))
        out["sentiment_momentum"] = float(np.clip(score - prior_score, -2.0, 2.0))
        hours = hours_to_next_macro_event(float(bar_time))
        # Normalize: 0 far away / unknown, → 1 as event approaches (within 48h).
        if hours is not None and hours >= 0:
            out["macro_event_proximity"] = float(np.clip(1.0 - (hours / 48.0), 0.0, 1.0))
    except Exception:
        pass
    return out


def sentiment_feature_matrix(
    candles: list[dict] | Any,
    symbol: str | None,
) -> dict[str, np.ndarray]:
    if hasattr(candles, "to_dict"):
        rows = candles.to_dict("records")
    else:
        rows = list(candles or [])
    n = len(rows)
    score = np.zeros(n, dtype=np.float64)
    mom = np.zeros(n, dtype=np.float64)
    prox = np.zeros(n, dtype=np.float64)
    if not symbol or n == 0:
        return {
            "sentiment_score_24h": score,
            "sentiment_momentum": mom,
            "macro_event_proximity": prox,
        }
    # Match per-bar path (neutral zeros when no history). Avoid per-bar DB storms
    # by caching on identical epoch buckets (1-minute).
    cache: dict[int, tuple[float, float, float]] = {}
    for i in range(n):
        t = _bar_unix(rows[i]) if isinstance(rows[i], dict) else None
        if t is None:
            continue
        key = int(t // 60.0)
        if key not in cache:
            feats = sentiment_features_for_epoch(symbol, t)
            cache[key] = (
                feats["sentiment_score_24h"],
                feats["sentiment_momentum"],
                feats["macro_event_proximity"],
            )
        score[i], mom[i], prox[i] = cache[key]
    return {
        "sentiment_score_24h": score,
        "sentiment_momentum": mom,
        "macro_event_proximity": prox,
    }


# ── Cross-asset peers ────────────────────────────────────────────────────


def _is_crypto_symbol(symbol: str) -> bool:
    sym = symbol.upper()
    if sym.endswith("USDT") or sym.endswith("USD") and len(sym) > 4:
        try:
            from app.services.massive_symbols import is_crypto_terminal_symbol
            return bool(is_crypto_terminal_symbol(sym))
        except Exception:
            return sym.endswith("USDT")
    return False


def _fallback_peers(symbol: str, top_k: int = 3) -> list[str]:
    sym = symbol.upper()
    basket = list(_CRYPTO_PEER_BASKET if _is_crypto_symbol(sym) else _EQUITY_PEER_BASKET)
    peers = [p for p in basket if p != sym]
    return peers[:top_k]


def resolve_peer_symbols(symbol: str, top_k: int = 3) -> list[str]:
    """Dynamic peers from GNN correlation matrix when available; else hardcoded."""
    sym = (symbol or "").upper()
    if not sym:
        return []
    peers = _peers_from_gnn(sym, top_k=top_k)
    if peers:
        return peers
    return _fallback_peers(sym, top_k=top_k)


def _peers_from_gnn(symbol: str, top_k: int = 3) -> list[str]:
    try:
        from app.config import DATA_DIR
        root = os.path.join(str(DATA_DIR), "gnn_cross_asset_models")
        if not os.path.isdir(root):
            return []
        best: list[tuple[float, str]] = []
        for dirpath, _dirs, files in os.walk(root):
            if "metadata.json" not in files:
                continue
            path = os.path.join(dirpath, "metadata.json")
            try:
                with open(path, encoding="utf-8") as fh:
                    meta = json.load(fh)
            except Exception:
                continue
            symbols = meta.get("symbols") or meta.get("basket_symbols") or []
            adj = meta.get("adjacency") or meta.get("correlation_matrix")
            if not symbols or adj is None:
                continue
            symbols = [str(s).upper() for s in symbols]
            if symbol not in symbols:
                continue
            try:
                mat = np.asarray(adj, dtype=np.float64)
            except Exception:
                continue
            if mat.ndim != 2 or mat.shape[0] != len(symbols):
                continue
            idx = symbols.index(symbol)
            row = mat[idx]
            ranked = sorted(
                (
                    (float(row[j]), symbols[j])
                    for j in range(len(symbols))
                    if symbols[j] != symbol and float(row[j]) > 0
                ),
                reverse=True,
            )
            for corr, peer in ranked[:top_k]:
                best.append((corr, peer))
        if not best:
            return []
        # Dedupe keeping highest corr
        seen: dict[str, float] = {}
        for corr, peer in best:
            if peer not in seen or corr > seen[peer]:
                seen[peer] = corr
        ordered = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)
        return [p for p, _ in ordered[:top_k]]
    except Exception:
        logger.debug("GNN peer resolve failed for %s", symbol, exc_info=True)
        return []


def _index_symbol(symbol: str) -> str:
    return _CRYPTO_INDEX if _is_crypto_symbol(symbol) else _EQUITY_INDEX


def _aligned_returns(
    times: np.ndarray,
    close: np.ndarray,
    peer_times: np.ndarray,
    peer_close: np.ndarray,
) -> np.ndarray:
    """Peer return at each source bar via asof last peer close (causal)."""
    n = len(close)
    out = np.zeros(n, dtype=np.float64)
    if n == 0 or len(peer_close) < 2:
        return out
    peer_ret = np.zeros(len(peer_close), dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        for i in range(1, len(peer_close)):
            if peer_close[i - 1] > 0:
                peer_ret[i] = (peer_close[i] - peer_close[i - 1]) / peer_close[i - 1]
    j = 0
    last = 0.0
    m = len(peer_times)
    for i in range(n):
        t = times[i]
        if np.isnan(t):
            out[i] = last
            continue
        while j < m and peer_times[j] <= t:
            last = float(peer_ret[j])
            j += 1
        out[i] = last
    return out


def cross_asset_feature_matrix(
    candles: list[dict] | Any,
    symbol: str | None,
    *,
    peer_candles: dict[str, list[dict]] | None = None,
) -> dict[str, np.ndarray]:
    if hasattr(candles, "to_dict"):
        rows = candles.to_dict("records")
    else:
        rows = list(candles or [])
    n = len(rows)
    empty = {k: np.zeros(n, dtype=np.float64) for k in CROSS_ASSET_FEATURE_NAMES}
    if n == 0 or not symbol:
        return empty

    close = np.array([_safe_float(r.get("close")) for r in rows], dtype=np.float64)
    times = np.array([(_bar_unix(r) or np.nan) for r in rows], dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        prev = np.roll(close, 1)
        prev[0] = close[0]
        own_ret = np.where(prev > 0, (close - prev) / prev, 0.0)

    peers = resolve_peer_symbols(symbol, top_k=3)
    peer_ret_mat: list[np.ndarray] = []
    for peer in peers:
        pc = (peer_candles or {}).get(peer) or (peer_candles or {}).get(peer.upper())
        if not pc:
            continue
        pt = np.array([(_bar_unix(b) or np.nan) for b in pc], dtype=np.float64)
        pclose = np.array([_safe_float(b.get("close")) for b in pc], dtype=np.float64)
        peer_ret_mat.append(_aligned_returns(times, close, pt, pclose))

    peer_avg = np.zeros(n, dtype=np.float64)
    if peer_ret_mat:
        stacked = np.column_stack(peer_ret_mat)
        # Average of last 5 peer returns (per bar already 1-bar return; smooth)
        for i in range(n):
            start = max(0, i - 4)
            peer_avg[i] = float(np.mean(stacked[start : i + 1]))

    divergence = own_ret - peer_avg

    # Rolling corr vs index
    corr20 = np.zeros(n, dtype=np.float64)
    idx_sym = _index_symbol(symbol)
    idx_c = (peer_candles or {}).get(idx_sym)
    if idx_c is None and peer_ret_mat:
        # Use first peer as proxy if index missing
        idx_series = peer_ret_mat[0]
    elif idx_c:
        pt = np.array([(_bar_unix(b) or np.nan) for b in idx_c], dtype=np.float64)
        pclose = np.array([_safe_float(b.get("close")) for b in idx_c], dtype=np.float64)
        idx_series = _aligned_returns(times, close, pt, pclose)
    else:
        idx_series = None
    if idx_series is not None:
        for i in range(20, n):
            a = own_ret[i - 20 : i]
            b = idx_series[i - 20 : i]
            if float(np.std(a)) < 1e-12 or float(np.std(b)) < 1e-12:
                continue
            corr20[i] = float(np.clip(np.corrcoef(a, b)[0, 1], -1.0, 1.0))

    return {
        "peer_returns_avg": peer_avg,
        "peer_divergence": divergence,
        "correlation_rolling_20": corr20,
    }


# ── Crypto derivatives ───────────────────────────────────────────────────


def crypto_deriv_features_for_epoch(symbol: str | None, bar_time: float | None) -> dict[str, float]:
    out = {"funding_rate_norm": 0.0, "oi_change_24h_norm": 0.0}
    if not symbol:
        return out
    try:
        from app.services.massive_symbols import is_crypto_terminal_symbol
        if not is_crypto_terminal_symbol(symbol):
            return out
        from app.services.altdata.store import get_crypto_derivatives_at
        snap = get_crypto_derivatives_at(symbol, bar_time)
        if not snap:
            return out
        fr = _safe_float(snap.get("funding_rate"), 0.0)
        oi = _safe_float(snap.get("oi_change_24h_pct"), 0.0)
        # Typical funding ~1e-4; normalize into roughly [-1, 1]
        out["funding_rate_norm"] = float(np.clip(fr / 0.001, -1.0, 1.0))
        out["oi_change_24h_norm"] = float(np.clip(oi / 20.0, -1.0, 1.0))
    except Exception:
        pass
    return out


def crypto_deriv_feature_matrix(
    candles: list[dict] | Any,
    symbol: str | None,
) -> dict[str, np.ndarray]:
    if hasattr(candles, "to_dict"):
        rows = candles.to_dict("records")
    else:
        rows = list(candles or [])
    n = len(rows)
    fr = np.zeros(n, dtype=np.float64)
    oi = np.zeros(n, dtype=np.float64)
    if not symbol or n == 0:
        return {"funding_rate_norm": fr, "oi_change_24h_norm": oi}
    cache: dict[int, tuple[float, float]] = {}
    for i in range(n):
        t = _bar_unix(rows[i]) if isinstance(rows[i], dict) else None
        if t is None:
            continue
        key = int(t // 60.0)
        if key not in cache:
            feats = crypto_deriv_features_for_epoch(symbol, t)
            cache[key] = (feats["funding_rate_norm"], feats["oi_change_24h_norm"])
        fr[i], oi[i] = cache[key]
    return {"funding_rate_norm": fr, "oi_change_24h_norm": oi}


# Last-bar live path only needs ~500 priors (frac-diff window). Capping here
# keeps evaluate() O(window) instead of replaying the full 1500-bar deque.
_LIVE_ADVANCED_HISTORY_CAP = 512


def advanced_features_for_bar(
    current: dict,
    history_rows: list[dict] | None = None,
    *,
    symbol: str | None = None,
    peer_candles: dict[str, list[dict]] | None = None,
) -> dict[str, float]:
    """Per-bar Phase 1–3 advanced features (HTF excluded — see ml_feature_htf)."""
    rows = list(history_rows or [])
    if len(rows) > _LIVE_ADVANCED_HISTORY_CAP:
        rows = rows[-_LIVE_ADVANCED_HISTORY_CAP:]
    series = rows + [dict(current)]
    o = np.array([_safe_float(r.get("open")) for r in series], dtype=np.float64)
    h = np.array([_safe_float(r.get("high")) for r in series], dtype=np.float64)
    l = np.array([_safe_float(r.get("low")) for r in series], dtype=np.float64)
    c = np.array([_safe_float(r.get("close")) for r in series], dtype=np.float64)
    v = np.array([_safe_float(r.get("volume")) for r in series], dtype=np.float64)

    out: dict[str, float] = {}
    for k, arr in realized_vol_feature_matrix(o, h, l, c).items():
        out[k] = float(arr[-1])
    for k, arr in fracdiff_feature_matrix(c, v).items():
        out[k] = float(arr[-1])
    for k, arr in info_theory_feature_matrix(c).items():
        out[k] = float(arr[-1])
    for k, arr in structure_feature_matrix(h, l, c).items():
        out[k] = float(arr[-1])

    sym = symbol or str(current.get("_symbol") or current.get("symbol") or "")
    t = _bar_unix(current)
    out.update(sentiment_features_for_epoch(sym or None, t))
    out.update(crypto_deriv_features_for_epoch(sym or None, t))

    xa = cross_asset_feature_matrix(series, sym or None, peer_candles=peer_candles)
    for k, arr in xa.items():
        out[k] = float(arr[-1])
    return out
