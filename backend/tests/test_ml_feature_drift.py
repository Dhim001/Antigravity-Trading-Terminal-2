"""APP_SCAN #7 — live inference must feed FeatureDriftMonitor."""

from __future__ import annotations

import numpy as np

from app.services.bots.ml_feature_drift import (
    FeatureDriftMonitor,
    record_ml_inference_features,
)
from app.services.bots.ml_feature_engineering import SIGNAL_FEATURE_NAMES


def test_record_ml_inference_features_fills_buffer():
    mon = FeatureDriftMonitor(window_size=50)
    # Isolate from process singleton by patching get inside helper via direct API.
    vec = [float(i) for i in range(len(SIGNAL_FEATURE_NAMES))]
    for _ in range(5):
        mon.record_inference("ETHUSDT", "LSTM_DIRECTION", vec)
    key = mon._key("ETHUSDT", "LSTM_DIRECTION")
    assert len(mon._buffers[key]) == 5


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
