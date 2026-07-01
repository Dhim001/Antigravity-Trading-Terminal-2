"""Anomaly detection on OHLCV bars."""

import unittest

import pandas as pd

from app.services.agent.anomaly_detector import detect_bar_anomaly


def _make_df(closes: list[float], volumes: list[float]) -> pd.DataFrame:
    times = list(range(len(closes)))
    return pd.DataFrame({
        "time": times,
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": volumes,
    })


class AnomalyDetectorTests(unittest.TestCase):
    def test_no_anomaly_on_flat_series(self):
        closes = [100.0] * 30
        volumes = [1000.0] * 30
        df = _make_df(closes, volumes)
        result = detect_bar_anomaly(df, 29)
        self.assertFalse(result["is_anomaly"])

    def test_volume_spike_detected(self):
        closes = [100.0 + i * 0.01 for i in range(30)]
        volumes = [1000.0] * 29 + [50000.0]
        df = _make_df(closes, volumes)
        result = detect_bar_anomaly(df, 29)
        self.assertTrue(result["is_anomaly"])
        self.assertIn("volume_spike", result["kinds"])

    def test_return_spike_detected(self):
        closes = [100.0] * 29 + [108.0]
        volumes = [1000.0] * 30
        df = _make_df(closes, volumes)
        result = detect_bar_anomaly(df, 29)
        self.assertTrue(result["is_anomaly"])
        self.assertTrue("return_spike" in result["kinds"] or "price_gap" in result["kinds"])


if __name__ == "__main__":
    unittest.main()
