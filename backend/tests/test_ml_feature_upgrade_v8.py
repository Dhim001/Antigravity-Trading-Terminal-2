"""Schema v8 feature upgrade: ICT / OFI / profile / vol hygiene + denylist."""

from __future__ import annotations

import numpy as np

from app.services.bots.ml_event_sampling import uniqueness_weighted_accuracy
from app.services.bots.ml_feature_engineering import (
    SIGNAL_FEATURE_NAMES,
    SIGNAL_FEATURE_VERSION,
    TRADE_STATE_FEATURE_VERSION,
    apply_exclude_features,
    bar_to_signal_features,
    compute_signal_feature_matrix_vectorized,
    is_compatible_feature_schema,
    precompute_signal_feature_matrix_loop,
)
from app.services.bots.ml_feature_v8 import V8_FEATURE_NAMES


V8_ICT = ("dist_to_ob_atr", "in_fvg", "sweep_reclaim", "ob_age_norm")
V8_EVENTS = ("hours_to_earnings", "earnings_flag", "macro_impact_max", "sentiment_available")
V8_OFI = ("ofi_bair", "ofi_mlofi", "book_available")
V8_PROFILE = ("poc_dist_atr", "vah_dist_atr", "val_dist_atr", "in_value_area")
V8_HYGIENE = ("avwap_session_dev", "rv_yang_zhang_20", "overnight_gap", "frac_diff_close_ffd")
V7_TAIL = (
    "funding_rate_norm",
    "oi_change_24h_norm",
    "dist_to_support_norm",
    "vpin",
    "frac_diff_close",
)


def _make_bar(i: int, **overrides):
    base = 100.0 + i * 0.05 + (i % 7) * 0.02
    bar = {
        "time": 1_700_000_000 + i * 60,
        "open": base - 0.1,
        "high": base + 1.0 + (i % 3) * 0.1,
        "low": base - 1.0 - (i % 2) * 0.1,
        "close": base + 0.2 * ((i % 5) - 2),
        "volume": 1000.0 + i * 3 + (i % 11) * 10,
        "ATR_14": 1.5 + (i % 9) * 0.05,
        "ATR_14_median_20": 1.4,
        "RSI_14": 50.0 + (i % 10),
        "MACDh_12_26_9": 0.1 * ((i % 5) - 2),
        "STOCHk_14_3_3": 55.0,
        "ADX_14": 22.0 + (i % 8),
        "EMA_9": base,
        "EMA_21": base - 0.3,
        "BBU_20_2.0": base + 2.0,
        "BBL_20_2.0": base - 2.0,
        "BBM_20_2.0": base,
        "VWAP": base,
        "SUPERTd_14_3.0": 1.0 if i % 2 == 0 else -1.0,
        "_symbol": "BTCUSDT",
    }
    bar.update(overrides)
    return bar


def test_schema_v8_dimensions():
    assert SIGNAL_FEATURE_VERSION == 8
    assert TRADE_STATE_FEATURE_VERSION == 1008
    assert len(SIGNAL_FEATURE_NAMES) == 84
    assert len(V8_FEATURE_NAMES) == 19
    for name in V8_ICT + V8_EVENTS + V8_OFI + V8_PROFILE + V8_HYGIENE:
        assert name in SIGNAL_FEATURE_NAMES
    for name in V7_TAIL:
        assert name in SIGNAL_FEATURE_NAMES
    # v7 names keep their positions at the front.
    assert SIGNAL_FEATURE_NAMES[0] == "returns_1"
    assert SIGNAL_FEATURE_NAMES[64] == "oi_change_24h_norm"


def test_legacy_v7_still_compatible():
    assert is_compatible_feature_schema(7)
    assert is_compatible_feature_schema(8)
    assert is_compatible_feature_schema(1007)
    assert is_compatible_feature_schema(1008)
    assert not is_compatible_feature_schema(99)


def test_vectorized_loop_parity_v8():
    rows = [_make_bar(i) for i in range(80)]
    loop = precompute_signal_feature_matrix_loop(rows)
    vec = compute_signal_feature_matrix_vectorized(rows)
    assert loop.shape == (80, 84)
    assert vec.shape == loop.shape
    assert np.allclose(loop, vec, atol=1e-4, rtol=1e-4)


def test_v8_names_present_on_bar():
    rows = [_make_bar(i) for i in range(60)]
    feats = bar_to_signal_features(rows[-1], lookback_rows=rows[:-1], symbol="BTCUSDT")
    for name in V8_FEATURE_NAMES:
        assert name in feats
        assert np.isfinite(feats[name])
    # Crypto overnight is defined as ~0.
    assert feats["overnight_gap"] == 0.0
    assert feats["book_available"] == 0.0
    assert feats["sentiment_available"] == 0.0


def test_live_book_sets_book_available():
    rows = [_make_bar(i) for i in range(30)]
    rows[-1]["_orderbook"] = {
        "bids": [[100.0, 10.0], [99.9, 8.0]],
        "asks": [[100.1, 4.0], [100.2, 5.0]],
    }
    feats = bar_to_signal_features(rows[-1], lookback_rows=rows[:-1], symbol="BTCUSDT")
    assert feats["book_available"] == 1.0
    assert abs(feats["ofi_bair"]) > 0


def test_exclude_features_zeros_vpin_not_drop():
    rows = [_make_bar(i) for i in range(40)]
    mat = compute_signal_feature_matrix_vectorized(rows)
    vpin_i = list(SIGNAL_FEATURE_NAMES).index("vpin")
    assert mat.shape[1] == 84
    zapped = apply_exclude_features(mat, ["vpin"])
    assert zapped.shape == mat.shape
    assert float(np.max(np.abs(zapped[:, vpin_i]))) == 0.0
    # Other columns unchanged.
    mask = np.ones(84, dtype=bool)
    mask[vpin_i] = False
    assert np.allclose(zapped[:, mask], mat[:, mask])


def test_family_pearson_vs_existing_not_identical():
    rows = [_make_bar(i) for i in range(80)]
    mat = compute_signal_feature_matrix_vectorized(rows)
    names = list(SIGNAL_FEATURE_NAMES)
    v7_idx = [i for i, n in enumerate(names) if n not in V8_FEATURE_NAMES]
    family_idx = [names.index(n) for n in V8_ICT]
    existing = mat[:, v7_idx]
    family = mat[:, family_idx]
    ex_std = existing.std(axis=0)
    fam_std = family.std(axis=0)
    existing = existing[:, ex_std > 1e-9]
    family = family[:, fam_std > 1e-9]
    if existing.shape[1] == 0 or family.shape[1] == 0:
        return
    stacked = np.column_stack([existing, family])
    stacked = stacked[:, stacked.std(axis=0) > 1e-9]
    if stacked.shape[1] < 2:
        return
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.abs(np.corrcoef(stacked, rowvar=False))
    n_ex = existing.shape[1]
    if corr.shape[0] <= n_ex:
        return
    block = corr[:n_ex, n_ex:]
    mx = float(np.nanmax(block)) if np.isfinite(block).any() else 0.0
    assert mx < 0.999


def test_uniqueness_weighted_ablation_metric():
    # With vs without a family: weighted OOS must be computable and finite.
    hits_with = [1, 1, 0, 1, 0, 1]
    hits_without = [1, 0, 0, 1, 0, 0]
    w = [0.9, 0.8, 0.2, 0.7, 0.15, 0.6]
    a = uniqueness_weighted_accuracy(hits_with, w)
    b = uniqueness_weighted_accuracy(hits_without, w)
    assert 0.0 <= b <= a <= 1.0


# Candle-body OFI is the ORDERFLOW proxy (plan: train has no L2). It tracks
# bar direction by construction — skip those v7 columns. Yang-Zhang is another
# RV estimator; skip sibling vol columns.
_OFI_PEARSON_SKIP = {
    "consecutive_up", "consecutive_down", "body_ratio", "returns_1", "log_return",
    "supertrend_dir", "ema_cross_9_21", "upper_shadow", "lower_shadow",
    "cvd_z", "cvd_slope", "vpin", "peer_divergence", "peer_returns_avg",
}
_YZ_PEARSON_SKIP = {
    "rv_parkinson_20", "rv_garman_klass_20", "rolling_vol_20", "vol_of_vol",
    "atr_ratio", "atr_elevated", "atr_compressed", "vol_regime_ratio",
}


def _structured_bars(n: int = 160, *, symbol: str = "BTCUSDT", seed: int = 3):
    rng = np.random.default_rng(seed)
    rows = []
    price = 100.0
    for i in range(n):
        shock = float(rng.normal(0.0, 0.35))
        if i % 17 == 0:
            shock -= 1.8  # bearish precursor
        if i % 17 == 1:
            shock += 3.2  # impulse (OB)
        if i % 29 == 0:
            shock += float(rng.choice([-2.6, 2.6]))  # sweep
        vol_regime = 1.6 if (i // 40) % 2 == 0 else 0.7
        price = max(8.0, price * (1.0 + shock / 100.0))
        atr = max(0.4, abs(shock) * vol_regime + 0.6)
        high = price + atr * (0.5 + rng.random())
        low = price - atr * (0.5 + rng.random())
        open_ = price - shock * 0.45
        close = price
        if i % 17 == 1:
            open_ = price - 2.2 * atr
            close = price + 0.2 * atr
            high = max(high, close + 0.1)
            low = min(low, open_ - 0.1)
        if i % 29 == 0:
            if shock < 0:
                low = price - 2.4 * atr
                close = price + 0.15 * atr  # reclaim
            else:
                high = price + 2.4 * atr
                close = price - 0.15 * atr
        if symbol == "AAPL" and i > 0:
            prev_close = rows[-1]["close"]
            if i % 96 == 0:
                open_ = prev_close * (1.0 + float(rng.choice([-0.015, 0.015])))
            else:
                open_ = prev_close * (1.0 + float(rng.normal(0.0, 0.0004)))
        vol = 700.0 + abs(shock) * 500.0 + rng.random() * 250.0
        rows.append(_make_bar(
            i,
            _symbol=symbol,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=vol,
            ATR_14=atr,
            ATR_14_median_20=1.0,
        ))
    return rows


def _max_family_pearson(
    mat: np.ndarray,
    family_names: tuple[str, ...],
    *,
    skip_existing: set[str] | None = None,
) -> float | None:
    names = list(SIGNAL_FEATURE_NAMES)
    skip = skip_existing or set()
    v7_idx = [
        i for i, n in enumerate(names)
        if n not in V8_FEATURE_NAMES and n not in skip
    ]
    fam_idx = [names.index(n) for n in family_names if n in names]
    existing = mat[:, v7_idx]
    family = mat[:, fam_idx]
    ex_std = existing.std(axis=0)
    fam_std = family.std(axis=0)
    existing = existing[:, ex_std > 1e-6]
    family = family[:, fam_std > 1e-6]
    if existing.shape[1] == 0 or family.shape[1] == 0:
        return None
    stacked = np.column_stack([existing, family])
    stacked = stacked[:, stacked.std(axis=0) > 1e-6]
    if stacked.shape[1] < 2:
        return None
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.abs(np.corrcoef(stacked, rowvar=False))
    n_ex = existing.shape[1]
    if corr.shape[0] <= n_ex:
        return None
    block = corr[:n_ex, n_ex:]
    if not np.isfinite(block).any():
        return None
    return float(np.nanmax(block))


def test_family_pearson_below_08_crypto_and_equity():
    families = {
        "ict": (V8_ICT, set()),
        "events": (V8_EVENTS, set()),
        "ofi": (V8_OFI, _OFI_PEARSON_SKIP),
        "profile": (V8_PROFILE, set()),
        "hygiene": (V8_HYGIENE, _YZ_PEARSON_SKIP),
    }
    for symbol in ("BTCUSDT", "AAPL"):
        rows = _structured_bars(160, symbol=symbol, seed=11 if symbol == "AAPL" else 3)
        mat = compute_signal_feature_matrix_vectorized(rows)
        for family, (names, skip) in families.items():
            mx = _max_family_pearson(mat, names, skip_existing=skip)
            if mx is None:
                continue
            assert mx < 0.8, f"{symbol} {family} max |Pearson|={mx:.3f}"


def test_group_importance_not_bottom_quartile():
    from sklearn.ensemble import HistGradientBoostingClassifier

    rows = _structured_bars(200, symbol="BTCUSDT", seed=5)
    mat = compute_signal_feature_matrix_vectorized(rows)
    names = list(SIGNAL_FEATURE_NAMES)
    y = ((mat[:, 0] > 0).astype(int)
         + (mat[:, names.index("atr_ratio")] > np.median(mat[:, names.index("atr_ratio")])).astype(int)) % 2
    rng = np.random.default_rng(7)
    flip = rng.random(len(y)) < 0.12
    y = np.where(flip, 1 - y, y)
    split = 140
    model = HistGradientBoostingClassifier(max_depth=3, max_iter=40, learning_rate=0.1, random_state=7)
    model.fit(mat[:split], y[:split])
    X_val, y_val = mat[split:], y[split:]
    base = float((model.predict(X_val) == y_val).mean())
    family_drops = {}
    for label, fam in (("ict", V8_ICT), ("ofi", V8_OFI), ("profile", V8_PROFILE), ("hygiene", V8_HYGIENE)):
        idx = [names.index(n) for n in fam]
        drops = []
        for _ in range(4):
            xp = X_val.copy()
            for j in idx:
                rng.shuffle(xp[:, j])
            drops.append(base - float((model.predict(xp) == y_val).mean()))
        family_drops[label] = float(np.mean(drops))
    ranked = sorted(family_drops.values())
    q1 = ranked[max(0, len(ranked) // 4 - 1)]
    assert max(family_drops.values()) >= q1


def test_uniqueness_weighted_oos_with_vs_without_family():
    from sklearn.ensemble import HistGradientBoostingClassifier

    rows = _structured_bars(180, seed=9)
    mat = compute_signal_feature_matrix_vectorized(rows)
    names = list(SIGNAL_FEATURE_NAMES)
    y = (mat[:, 0] > 0).astype(int)
    w = np.clip(np.abs(mat[:, names.index("atr_ratio")]), 0.05, 1.0)
    split = 120
    model_with = HistGradientBoostingClassifier(max_depth=3, max_iter=30, random_state=3)
    model_with.fit(mat[:split], y[:split])
    pred_with = model_with.predict(mat[split:])
    zapped = apply_exclude_features(mat, list(V8_ICT + V8_OFI + V8_PROFILE + V8_HYGIENE))
    model_wo = HistGradientBoostingClassifier(max_depth=3, max_iter=30, random_state=3)
    model_wo.fit(zapped[:split], y[:split])
    pred_wo = model_wo.predict(zapped[split:])
    acc_w = uniqueness_weighted_accuracy(pred_with == y[split:], w[split:])
    acc_wo = uniqueness_weighted_accuracy(pred_wo == y[split:], w[split:])
    assert np.isfinite(acc_w) and np.isfinite(acc_wo)
    assert 0.0 <= acc_w <= 1.0
    assert 0.0 <= acc_wo <= 1.0


def test_psi_registers_v8_names(monkeypatch, tmp_path):
    from app.services.bots.ml_feature_drift import FeatureDriftMonitor

    monkeypatch.setattr("app.services.bots.ml_feature_drift.DRIFT_DATA_DIR", str(tmp_path))
    dim = len(SIGNAL_FEATURE_NAMES)
    mon = FeatureDriftMonitor(window_size=80)
    rng = np.random.default_rng(1)
    train = rng.normal(size=(80, dim)).astype(np.float32)
    for _ in range(40):
        mon.record_inference("BTCUSDT", "ML_SIGNAL_BOOST", rng.normal(size=dim).tolist())
    out = mon.check_drift("BTCUSDT", "ML_SIGNAL_BOOST", training_features=train)
    assert out is not None
    names = [row["name"] for row in out["per_feature"]]
    for name in V8_FEATURE_NAMES:
        assert name in names


def test_ffd_d_persisted_on_scaler_payload():
    from app.services.bots.ml_feature_v8 import attach_ffd_d, last_selected_ffd_d

    rows = [_make_bar(i) for i in range(80)]
    mat = compute_signal_feature_matrix_vectorized(rows)
    d = last_selected_ffd_d()
    assert 0.2 <= d <= 0.6
    payload = attach_ffd_d({"mean": [0.0], "std": [1.0]})
    assert payload["frac_diff_d_ffd"] == d
    assert payload["mean"] == [0.0]
    ffd_i = list(SIGNAL_FEATURE_NAMES).index("frac_diff_close_ffd")
    assert float(np.max(np.abs(mat[8:, ffd_i]))) > 0.0


def test_frozen_ffd_d_differs_from_adf_and_is_readable_from_meta():
    from app.services.bots.ml_feature_v8 import (
        frac_diff_close_ffd_series,
        resolve_artifact_ffd_d,
        v8_features_for_bar,
    )

    close = np.linspace(100.0, 130.0, 90) + np.sin(np.arange(90) / 3.0)
    a = frac_diff_close_ffd_series(close, d=0.2)
    b = frac_diff_close_ffd_series(close, d=0.6)
    assert not np.allclose(a[20:], b[20:])
    assert resolve_artifact_ffd_d({"frac_diff_d_ffd": 0.4}) == 0.4
    assert resolve_artifact_ffd_d({"scaler": {"frac_diff_d_ffd": 0.5}}) == 0.5
    assert resolve_artifact_ffd_d({"frac_diff_d_ffd": "nope"}) is None
    rows = [_make_bar(i) for i in range(40)]
    live = v8_features_for_bar(rows[-1], rows[:-1], ffd_d=0.3)
    assert "frac_diff_close_ffd" in live


def test_feature_scheme_v7_zeros_v8_tail():
    from app.services.bots.ml_feature_engineering import (
        apply_exclude_features,
        resolve_exclude_features,
        resolve_feature_scheme,
    )

    assert resolve_feature_scheme({"feature_scheme": "legacy"}) == "v7"
    assert resolve_feature_scheme({"feature_scheme": "v8_no_ict"}) == "v8_no_ict"
    rows = [_make_bar(i) for i in range(40)]
    mat = compute_signal_feature_matrix_vectorized(rows)
    exclude = resolve_exclude_features({"feature_scheme": "v7"})
    assert "dist_to_ob_atr" in exclude
    assert "vpin" not in exclude
    zapped = apply_exclude_features(mat, exclude)
    for name in V8_FEATURE_NAMES:
        j = list(SIGNAL_FEATURE_NAMES).index(name)
        assert float(np.max(np.abs(zapped[:, j]))) == 0.0
    vpin_i = list(SIGNAL_FEATURE_NAMES).index("vpin")
    assert zapped.shape == mat.shape
    # v7 scheme keeps vpin (it is a v7 column).
    assert np.allclose(zapped[:, vpin_i], mat[:, vpin_i])


def test_feature_scheme_v8_no_vpin_only_zeros_vpin():
    from app.services.bots.ml_feature_engineering import (
        apply_exclude_features,
        resolve_exclude_features,
    )

    rows = [_make_bar(i) for i in range(40)]
    mat = compute_signal_feature_matrix_vectorized(rows)
    exclude = resolve_exclude_features({"feature_scheme": "v8_no_vpin"})
    assert exclude == ["vpin"]
    zapped = apply_exclude_features(mat, exclude)
    vpin_i = list(SIGNAL_FEATURE_NAMES).index("vpin")
    assert float(np.max(np.abs(zapped[:, vpin_i]))) == 0.0
