"""Tests for RL replay buffer + reward shaping (AI-FT-PTL-001 §3.2, P1 #4/#5)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import numpy as np
import pytest


@pytest.fixture
def tmp_replay_db(monkeypatch):
    """Fresh sqlite with just the rl_replay table, patched into the store."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("""
        CREATE TABLE rl_replay (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            obs TEXT NOT NULL,
            action INTEGER NOT NULL,
            reward REAL NOT NULL,
            next_obs TEXT,
            done INTEGER NOT NULL DEFAULT 0,
            outcome_class TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    from app.services.bots import rl_replay_store as m

    def _conn():
        c = sqlite3.connect(path, check_same_thread=False)
        return c

    monkeypatch.setattr(m, "get_connection", _conn)
    yield m
    conn.close()
    os.unlink(path)


class TestRecordTransition:
    def test_noop_when_disabled(self, tmp_replay_db):
        m = tmp_replay_db
        with mock.patch.object(m, "RL_REPLAY_ENABLED", False):
            m.record_transition(
                bot_id="b1", symbol="BTCUSDT", obs=[0.1], action=1, reward=0.5,
            )
            assert m.count_transitions("BTCUSDT") == 0

    def test_roundtrip(self, tmp_replay_db):
        m = tmp_replay_db
        with mock.patch.object(m, "RL_REPLAY_ENABLED", True):
            m.record_transition(
                bot_id="b1", symbol="ETHUSDT",
                obs=np.array([1.0, 2.0]), action=1, reward=0.25,
                next_obs=np.array([1.1, 2.1]), done=True, outcome_class="clean_win",
            )
            assert m.count_transitions("ETHUSDT") == 1
            rows = m.load_transitions("ETHUSDT")
            assert len(rows) == 1
            assert rows[0]["action"] == 1
            assert abs(rows[0]["reward"] - 0.25) < 1e-6
            assert rows[0]["done"] is True
            assert rows[0]["outcome_class"] == "clean_win"
            np.testing.assert_allclose(rows[0]["obs"], [1.0, 2.0], rtol=1e-5)


class TestRewardShaping:
    def test_outcome_modifiers_present(self):
        from app.services.bots.rl_replay_store import OUTCOME_REWARD_MODIFIERS

        assert OUTCOME_REWARD_MODIFIERS["clean_win"] == 0.002
        assert OUTCOME_REWARD_MODIFIERS["regime_mismatch"] == -0.003
        assert OUTCOME_REWARD_MODIFIERS["stop_too_tight"] == -0.001
        assert OUTCOME_REWARD_MODIFIERS["good_entry_bad_exit"] == -0.0015
        assert OUTCOME_REWARD_MODIFIERS["giveback_win"] == -0.001

    def test_live_close_applies_modifier(self, tmp_replay_db):
        m = tmp_replay_db
        m._pending_actions.clear()
        with mock.patch.object(m, "RL_REPLAY_ENABLED", True), \
             mock.patch.object(m, "TCA_REWARD_FEEDBACK_ENABLED", False):
            m.note_pending_action("b1", "SOLUSDT", np.array([0.5, 0.6, 1.0, 0.02, 0.1]), 1)
            m.record_live_close("b1", "SOLUSDT", reward=0.010, outcome_class="clean_win")
            rows = m.load_transitions("SOLUSDT")
            assert len(rows) == 1
            assert abs(rows[0]["reward"] - 0.012) < 1e-6  # 0.010 + 0.002 bonus
            assert rows[0]["next_obs"] is not None
            np.testing.assert_allclose(rows[0]["next_obs"][-3:], [0.0, 0.0, 0.0])

    def test_live_close_subtracts_is(self, tmp_replay_db):
        m = tmp_replay_db
        m._pending_actions.clear()
        with mock.patch.object(m, "RL_REPLAY_ENABLED", True), \
             mock.patch.object(m, "TCA_REWARD_FEEDBACK_ENABLED", True), \
             mock.patch(
                 "app.services.bots.execution_tca.mean_is_bps_for_symbol",
                 return_value=20.0,  # 20 bps = 0.002
             ):
            m.note_pending_action("b1", "ADAUSDT", np.array([0.5]), 2)
            m.record_live_close("b1", "ADAUSDT", reward=0.010, outcome_class=None)
            rows = m.load_transitions("ADAUSDT")
            assert abs(rows[0]["reward"] - 0.008) < 1e-6  # 0.010 − 0.002 IS

    def test_note_pending_writes_prior_step(self, tmp_replay_db):
        m = tmp_replay_db
        m._pending_actions.clear()
        with mock.patch.object(m, "RL_REPLAY_ENABLED", True), \
             mock.patch.object(m, "TCA_REWARD_FEEDBACK_ENABLED", False):
            m.note_pending_action("b1", "ETHUSDT", np.array([1.0, 0.0, 0.0, 0.0]), 1)
            m.note_pending_action("b1", "ETHUSDT", np.array([1.1, 1.0, 0.01, 0.1]), 0)
            mid = m.load_transitions("ETHUSDT")
            assert len(mid) == 1
            assert mid[0]["done"] is False
            np.testing.assert_allclose(mid[0]["next_obs"], [1.1, 1.0, 0.01, 0.1])
            m.record_live_close("b1", "ETHUSDT", reward=0.01, outcome_class="clean_win")
            rows = m.load_transitions("ETHUSDT")
            assert len(rows) == 2
            assert rows[-1]["done"] is True


class TestFinetuneGuard:
    def test_rejects_small_buffer(self):
        from app.services.bots import rl_ppo_trainer as t

        with mock.patch(
            "app.services.bots.rl_replay_store.count_transitions", return_value=10
        ), mock.patch("app.config.RL_REPLAY_MIN_FOR_FINETUNE", 1000):
            out = t.finetune_from_replay(
                model=None, optimizer=None, symbol="BTCUSDT", device="cpu",
            )
            assert out["applied"] is False
            assert "too small" in out["reason"]


if __name__ == "__main__":
    unittest.main()
