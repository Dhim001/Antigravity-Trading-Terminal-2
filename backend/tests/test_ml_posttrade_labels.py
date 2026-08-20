"""Tests for closed-loop post-trade label feedback (AI-FT-PTL-001 §4.2)."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

# Ensure a throwaway DB for these tests before app modules import it.
_tmp = tempfile.mkdtemp(prefix="ptl_test_")
os.environ["TRADING_DB_PATH"] = os.path.join(_tmp, "test.db")


class TestBarrierWidthScale(unittest.TestCase):
    def test_neutral_when_disabled(self):
        from app.services.bots import ml_posttrade_labels as m

        with mock.patch.object(m, "POSTTRADE_LABELS_ENABLED", False):
            assert m.barrier_width_scale("BTCUSDT") == 1.0

    def test_neutral_when_insufficient_samples(self):
        from app.services.bots import ml_posttrade_labels as m

        with mock.patch.object(m, "_fetch_recent_rows", return_value=[
            {"mae": 1.0, "mfe": 2.0, "outcome_class": "clean_win"},
        ] * 3):
            assert m.barrier_width_scale("BTCUSDT") == 1.0

    def test_scales_with_median_excursion(self):
        from app.services.bots import ml_posttrade_labels as m

        rows = [{"mae": 4.0, "mfe": 4.0, "outcome_class": "clean_win"}] * 10
        with mock.patch.object(m, "_fetch_recent_rows", return_value=rows):
            scale = m.barrier_width_scale("BTCUSDT")
            # median excursion 4.0 / 2.0 reference = 2.0 → clamped to max 1.6
            assert scale == 1.6


class TestHostileRegimeWindows(unittest.TestCase):
    def test_empty_when_disabled(self):
        from app.services.bots import ml_posttrade_labels as m

        with mock.patch.object(m, "POSTTRADE_LABELS_ENABLED", False):
            assert m.hostile_regime_windows("BTCUSDT") == []

    def test_window_around_regime_mismatch(self):
        from app.services.bots import ml_posttrade_labels as m

        rows = [{"bar_time": 1_000_000, "outcome_class": "regime_mismatch"}]
        with mock.patch.object(m, "_fetch_recent_rows", return_value=rows):
            wins = m.hostile_regime_windows("BTCUSDT")
            assert len(wins) == 1
            start, end = wins[0]
            assert start == 1_000_000 - 86400
            assert end == 1_000_000 + 86400

    def test_ignores_non_hostile(self):
        from app.services.bots import ml_posttrade_labels as m

        rows = [{"bar_time": 1_000_000, "outcome_class": "clean_win"}]
        with mock.patch.object(m, "_fetch_recent_rows", return_value=rows):
            assert m.hostile_regime_windows("BTCUSDT") == []


class TestApplyPosttradeFeedback(unittest.TestCase):
    def _labels(self):
        return [
            {"time": 1_000_000, "label": 1, "label_name": "BUY", "barrier_hit": "upper", "uniqueness": 1.0},
            {"time": 2_000_000, "label": -1, "label_name": "SELL", "barrier_hit": "lower", "uniqueness": 1.0},
        ]

    def test_noop_when_disabled(self):
        from app.services.bots import ml_posttrade_labels as m

        with mock.patch.object(m, "POSTTRADE_LABELS_ENABLED", False):
            out = m.apply_posttrade_feedback([], self._labels(), symbol="BTCUSDT")
            assert out[0]["label"] == 1

    def test_downweights_hostile_window(self):
        from app.services.bots import ml_posttrade_labels as m

        with mock.patch.object(m, "POSTTRADE_LABELS_ENABLED", True), \
             mock.patch.object(m, "mean_execution_shortfall_bps", return_value=None), \
             mock.patch.object(m, "hostile_regime_windows", return_value=[(999_999, 1_000_001)]), \
             mock.patch.object(m, "POSTTRADE_LABELS_HOSTILE_WEIGHT", 0.4):
            out = m.apply_posttrade_feedback([], self._labels(), symbol="BTCUSDT")
            assert out[0]["uniqueness"] == 0.4
            assert out[1]["uniqueness"] == 1.0

    def test_excludes_unreliable_execution(self):
        from app.services.bots import ml_posttrade_labels as m

        with mock.patch.object(m, "POSTTRADE_LABELS_ENABLED", True), \
             mock.patch.object(m, "mean_execution_shortfall_bps", return_value=80.0), \
             mock.patch.object(m, "hostile_regime_windows", return_value=[]), \
             mock.patch.object(m, "POSTTRADE_LABELS_MAX_IS_BPS", 50.0):
            out = m.apply_posttrade_feedback([], self._labels(), symbol="BTCUSDT")
            assert all(l["barrier_hit"] == "invalid" for l in out)


if __name__ == "__main__":
    unittest.main()
