"""Tests for P1 #7 conformal recalibration + P1 #8 cross-strategy transfer."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest import mock

_tmp = tempfile.mkdtemp(prefix="p1_gate_test_")
os.environ["TRADING_DB_PATH"] = os.path.join(_tmp, "test.db")


class TestConformalRecalibration(unittest.TestCase):
    def test_insufficient_outcomes(self):
        from app.services.bots import conformal_gate as cg

        with mock.patch.object(cg, "load_conformal", return_value=None), \
             mock.patch("app.config.CONFORMAL_RECALIB_ENABLED", True), \
             mock.patch(
                 "app.services.bots.meta_label_operational._rolling_predictions",
                 {"b1": [{"predicted": 0.7, "actual": True, "ts": 1.0}] * 5},
             ):
            out = cg.recalibrate_conformal_gate("b1")
            assert out["updated"] is False

    def test_ema_blend_with_existing(self):
        from app.services.bots import conformal_gate as cg

        existing = cg.ConformalCalibration(q_hat=0.40, threshold=0.60, n=100, alpha=0.10)
        # All confident + correct → low nonconformity → low fresh q_hat.
        preds = [{"predicted": 0.95, "actual": True, "ts": 1.0}] * 60
        saved: dict = {}

        def _save(bot_id, cal, *, path=None):
            saved["cal"] = cal

        with mock.patch.object(cg, "load_conformal", return_value=existing), \
             mock.patch.object(cg, "save_conformal", side_effect=_save), \
             mock.patch("app.config.CONFORMAL_RECALIB_ENABLED", True), \
             mock.patch("app.config.CONFORMAL_RECALIB_EMA_ALPHA", 0.2), \
             mock.patch(
                 "app.services.bots.meta_label_operational._rolling_predictions",
                 {"b1": preds},
             ):
            out = cg.recalibrate_conformal_gate("b1")
            assert out["updated"] is True
            new_q = saved["cal"].q_hat
            # Fresh q_hat from all-confident-correct preds = 0.05.
            # EMA: 0.8·0.40 + 0.2·0.05 = 0.33.
            assert abs(new_q - 0.33) < 1e-6


class TestCrossStrategyTransfer(unittest.TestCase):
    def setUp(self):
        from app.services.bots import agent_event_subscribers as s

        s._regime_mismatch_events.clear()
        s._regime_warning_until.clear()

    def test_warning_after_threshold(self):
        from app.services.bots import agent_event_subscribers as s

        with mock.patch("app.config.REGIME_WARNING_MIN_LESSONS", 3), \
             mock.patch("app.config.REGIME_WARNING_WINDOW_SEC", 86400.0):
            assert s.note_regime_mismatch("BTCUSDT") == 1
            assert s.note_regime_mismatch("BTCUSDT") == 2
            assert not s.regime_warning_active("BTCUSDT")
            assert s.note_regime_mismatch("BTCUSDT") == 3

            s.mark_regime_warning("BTCUSDT", time.time() + 3600)
            assert s.regime_warning_active("BTCUSDT")
            assert not s.regime_warning_active("ETHUSDT")

    def test_warning_expires(self):
        from app.services.bots import agent_event_subscribers as s

        s.mark_regime_warning("BTCUSDT", time.time() - 1)
        assert not s.regime_warning_active("BTCUSDT")

    def test_window_prunes_old_events(self):
        from app.services.bots import agent_event_subscribers as s

        now = time.time()
        with mock.patch("app.config.REGIME_WARNING_WINDOW_SEC", 100.0):
            s.note_regime_mismatch("SOLUSDT", ts=now - 200)  # outside window
            assert s.note_regime_mismatch("SOLUSDT", ts=now) == 1


if __name__ == "__main__":
    unittest.main()
