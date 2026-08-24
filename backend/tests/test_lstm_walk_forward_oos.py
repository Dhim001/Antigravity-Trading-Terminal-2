"""LSTM walk-forward folds must score in-memory, not via live ONNX."""

from __future__ import annotations

import numpy as np
import pytest


def _candles(n: int = 90) -> list[dict]:
    rng = np.random.default_rng(0)
    candles = []
    price = 100.0
    for i in range(n):
        price *= 1.0 + float(rng.normal(0.0002, 0.01))
        candles.append({
            "time": 1_700_000_000 + i * 300,
            "open": price,
            "high": price * 1.002,
            "low": price * 0.998,
            "close": price,
            "volume": 1000.0,
            "ATR_14": 1.2,
        })
    return candles


def test_evaluate_oos_lstm_uses_in_memory_bundle():
    torch = pytest.importorskip("torch")
    from app.services.bots.ml_feature_engineering import SIGNAL_FEATURE_NAMES
    from app.services.bots.ml_walk_forward_validator import evaluate_oos_accuracy

    class ConstBuy(torch.nn.Module):
        def forward(self, x):
            out = torch.zeros(x.shape[0], 3)
            out[:, 0] = 8.0
            return out

    n_feat = len(SIGNAL_FEATURE_NAMES)
    lookback = 8
    candles = _candles(80)
    feat = np.zeros((len(candles), n_feat), dtype=np.float32)
    labels = [
        {"label": 1, "uniqueness": 1.0, "is_event": True, "t1_idx": i + 3}
        for i in range(len(candles))
    ]
    bundle = {
        "strategy": "LSTM_DIRECTION",
        "model": ConstBuy(),
        "mean": [0.0] * n_feat,
        "std": [1.0] * n_feat,
        "lookback": lookback,
        "reverse_map": {0: "BUY", 1: "NONE", 2: "SELL"},
        "min_confidence": 0.0,
    }
    out = evaluate_oos_accuracy(
        "LSTM_DIRECTION",
        candles,
        {
            "_wf_mode": True,
            "_precomputed_features": feat,
            "_precomputed_labels": labels,
            "lookback": lookback,
            "min_confidence": 0.0,
        },
        train_result={"_wf_bundle": bundle},
    )
    assert out["buy_count"] > 0
    assert out["total_bars"] == len(candles)
    assert 0.0 <= out["accuracy"] <= 1.0
