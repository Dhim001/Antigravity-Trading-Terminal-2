"""APP_SCAN #7 — live inference must feed FeatureDriftMonitor."""

from __future__ import annotations

import numpy as np

from app.services.bots.ml_feature_drift import (
    FeatureDriftMonitor,
    record_ml_inference_features,
)
from app.services.bots.ml_feature_engineering import SIGNAL_FEATURE_NAMES


def test_record_ml_inference_features_fills_buffer(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.bots.ml_feature_drift.DRIFT_DATA_DIR", str(tmp_path))
    mon = FeatureDriftMonitor(window_size=50)
    vec = [float(i) for i in range(len(SIGNAL_FEATURE_NAMES))]
    for _ in range(5):
        mon.record_inference("ETHUSDT", "LSTM_DIRECTION", vec)
    key = mon._key("ETHUSDT", "LSTM_DIRECTION")
    assert len(mon._buffers[key]) == 5


def test_drift_buffer_drops_stale_schema_widths(monkeypatch, tmp_path):
    """Schema bumps must not mix vector widths (numpy PSI would raise)."""
    monkeypatch.setattr("app.services.bots.ml_feature_drift.DRIFT_DATA_DIR", str(tmp_path))
    mon = FeatureDriftMonitor(window_size=50)
    key = mon._key("AAPL", "ML_SIGNAL_BOOST")
    mon._buffers[key] = [[0.0] * 34 for _ in range(40)]
    mon._last_access[key] = 0.0

    new_vec = [1.0] * len(SIGNAL_FEATURE_NAMES)
    mon.record_inference("AAPL", "ML_SIGNAL_BOOST", new_vec)
    assert all(len(v) == len(SIGNAL_FEATURE_NAMES) for v in mon._buffers[key])
    assert len(mon._buffers[key]) == 1

    # check_drift must not raise on leftover mixed disk data
    mon._buffers[key] = [[0.0] * 34 for _ in range(20)] + [[1.0] * len(SIGNAL_FEATURE_NAMES) for _ in range(35)]
    out = mon.check_drift("AAPL", "ML_SIGNAL_BOOST", training_features=np.ones((50, len(SIGNAL_FEATURE_NAMES)), dtype=np.float32))
    assert out is not None
    assert out["n_live"] >= 30


def test_record_helper_accepts_dict_and_ndarray(monkeypatch):
    recorded: list[tuple] = []

    class _Fake:
        def record_inference(self, symbol, strategy, features):
            recorded.append((symbol, strategy, list(features)))

    monkeypatch.setattr(
        "app.services.bots.ml_feature_drift.get_feature_drift_monitor",
        lambda: _Fake(),
    )

    record_ml_inference_features("btcusdt", "ml_signal_boost", {SIGNAL_FEATURE_NAMES[0]: 1.5})
    assert recorded[0][0] == "BTCUSDT"
    assert recorded[0][1] == "ML_SIGNAL_BOOST"
    assert len(recorded[0][2]) == len(SIGNAL_FEATURE_NAMES)
    assert recorded[0][2][0] == 1.5

    record_ml_inference_features("ETHUSDT", "LSTM_DIRECTION", np.ones(len(SIGNAL_FEATURE_NAMES)))
    assert recorded[1][2][0] == 1.0
