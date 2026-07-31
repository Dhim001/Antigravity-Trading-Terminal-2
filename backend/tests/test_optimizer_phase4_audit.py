"""Optimizer Phase 4 audit fixes (2026-07-28):

1. stress_pnl no longer double-counts commissions (total_pnl is already fee-net).
2. Bayesian warm-start matches scores for params-sourced seeds (was silently dropped).
3. Trial-budget resolvers tolerate malformed user JSON values (no 500).
4. apply-optimized-config warns on strategy/symbol mismatch.
"""

import asyncio
import json
import types
import unittest
import unittest.mock

import pytest

from app.services.bots import optimization_store
from app.services.bots.backtest_multi_objective import stress_pnl_value
from app.services.bots.backtest_trial_budget import (
    resolve_max_trials,
    resolve_time_budget_sec,
)


# ── Fix 1: stress_pnl fee double-count ────────────────────────────────────


def test_stress_pnl_does_not_double_count_fees():
    row = {
        "total_pnl": 100.0,
        "trade_count": 10,
        "summary": {"total_fees": 5.0, "slippage_bps": 10},
        "config": {"allocation": 1000, "slippage_bps": 10},
    }
    # Stress = pnl − extra slippage only: 100 − 10×1000×(10/10000) = 90.
    # The buggy formula subtracted total_fees again → 85.
    assert stress_pnl_value(row) == pytest.approx(90.0)


def test_stress_pnl_uses_config_slippage_fallback():
    row = {
        "total_pnl": 50.0,
        "trade_count": 4,
        "summary": {},
        "config": {"allocation": 5000, "slippage_bps": 5},
    }
    assert stress_pnl_value(row) == pytest.approx(50.0 - 4 * 5000 * 0.0005)


# ── Fix 2: warm-start params matching ─────────────────────────────────────


def test_warm_start_enqueues_params_sourced_seeds(monkeypatch):
    optuna = pytest.importorskip("optuna")
    from app.services.bots.backtest_bayesian import _warm_start_study

    fake_run = {
        "run_id": "r1",
        "best_config": None,
        # Rows carry "params" (no "config") — the pre-fix matcher dropped these.
        "results": [
            {"params": {"x": 2}, "total_pnl": 5.0},
            {"params": {"x": 3}, "total_pnl": 7.0},
        ],
    }
    monkeypatch.setattr(
        optimization_store, "get_optimization_run", lambda rid: fake_run,
    )
    study = optuna.create_study(direction="maximize")
    enqueued = _warm_start_study(
        study,
        {"bayesian_warm_start_run_id": "r1"},
        [("x", [1, 2, 3])],
        {},
    )
    assert enqueued == 2
    values = sorted(t.value for t in study.trials)
    assert values == [5.0, 7.0]


def test_warm_start_still_matches_config_rows(monkeypatch):
    optuna = pytest.importorskip("optuna")
    from app.services.bots.backtest_bayesian import _warm_start_study

    fake_run = {
        "run_id": "r1",
        "best_config": {"x": 1},
        "results": [{"config": {"x": 1}, "total_pnl": 3.0}],
    }
    monkeypatch.setattr(
        optimization_store, "get_optimization_run", lambda rid: fake_run,
    )
    study = optuna.create_study(direction="maximize")
    enqueued = _warm_start_study(
        study, {"bayesian_warm_start_run_id": "r1"}, [("x", [1, 2, 3])], {},
    )
    assert enqueued == 1
    assert study.trials[0].value == 3.0


# ── Fix 3: malformed budget values ────────────────────────────────────────


def test_resolve_time_budget_tolerates_garbage():
    from app.config import BACKTEST_SWEEP_TIME_BUDGET_SEC

    assert resolve_time_budget_sec({"time_budget_sec": "abc"}) == float(
        BACKTEST_SWEEP_TIME_BUDGET_SEC
    )
    assert resolve_time_budget_sec({"time_budget_sec": None}) == float(
        BACKTEST_SWEEP_TIME_BUDGET_SEC
    )
    assert resolve_time_budget_sec({"time_budget_sec": "120"}) == 120.0


def test_resolve_max_trials_tolerates_garbage():
    from app.services.bots.backtest_sweep import MAX_SWEEP_COMBOS_EXTENDED

    # Garbage max_combos falls back to the legacy cap instead of raising.
    assert (
        resolve_max_trials({"max_combos": "oops", "sweep_mode": "bayesian"}, "bayesian")
        == MAX_SWEEP_COMBOS_EXTENDED
    )
    # Garbage max_trials falls back to the effective cap.
    got = resolve_max_trials({"max_trials": "NaN-ish", "sweep_mode": "sobol"}, "sobol")
    assert got >= 1
    # Sane values still respected.
    assert resolve_max_trials({"max_trials": 7, "sweep_mode": "sobol"}, "sobol") == 7


# ── Fix 4: apply-optimized-config mismatch warnings ───────────────────────


class _FakeMgr:
    def __init__(self, bot):
        self._bot = bot
        self.updated = None

    def get_bot_detail(self, bot_id):
        return {"bot": self._bot}

    async def update_bot_config(self, bot_id, patch):
        self.updated = patch
        return {"bot": {**self._bot, "config": patch}}


def _fake_request(bot_id, body, bot):
    mgr = _FakeMgr(bot)
    req = types.SimpleNamespace(
        path_params={"bot_id": bot_id},
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(
                terminal=types.SimpleNamespace(bot_manager=mgr),
            ),
        ),
    )

    async def _json():
        return body

    req.json = _json
    return req, mgr


def _patch_store(monkeypatch, run):
    monkeypatch.setattr(optimization_store, "get_optimization_run", lambda rid: run)
    monkeypatch.setattr(
        optimization_store,
        "get_best_config",
        lambda rid, source="best": dict(run.get("best_config") or {}),
    )
    monkeypatch.setattr(
        optimization_store, "link_optimization_to_bot", lambda *a, **k: True,
    )


class TestApplyOptimizedConfigWarnings(unittest.TestCase):
    def test_warns_on_strategy_and_symbol_mismatch(self):
        pytest.importorskip("starlette")
        from app.api.http import app as app_module

        run = {
            "run_id": "r1",
            "strategy": "LSTM_DIRECTION",
            "symbol": "AAPL",
            "best_config": {"learning_rate": 0.001},
        }
        with unittest.mock.patch.object(
            optimization_store, "get_optimization_run", lambda rid: run,
        ), unittest.mock.patch.object(
            optimization_store,
            "get_best_config",
            lambda rid, source="best": {"learning_rate": 0.001},
        ), unittest.mock.patch.object(
            optimization_store, "link_optimization_to_bot", lambda *a, **k: True,
        ):
            bot = {
                "symbol": "ETHUSDT",
                "strategy": "MACD_RSI",
                "status": "PAUSED",
                "config": {},
            }
            req, mgr = _fake_request("b1", {"optimization_run_id": "r1"}, bot)
            resp = asyncio.run(app_module.apply_optimized_config_handler(req))

        payload = json.loads(resp.body)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["warnings"]), 2)
        self.assertIn("LSTM_DIRECTION", payload["warnings"][0])
        self.assertIn("AAPL", payload["warnings"][1])
        self.assertEqual(mgr.updated, {"learning_rate": 0.001})

    def test_no_warnings_on_match(self):
        pytest.importorskip("starlette")
        from app.api.http import app as app_module

        run = {
            "run_id": "r1",
            "strategy": "MACD_RSI",
            "symbol": "ETHUSDT",
            "best_config": {"rsi_period": 21},
        }
        with unittest.mock.patch.object(
            optimization_store, "get_optimization_run", lambda rid: run,
        ), unittest.mock.patch.object(
            optimization_store,
            "get_best_config",
            lambda rid, source="best": {"rsi_period": 21},
        ), unittest.mock.patch.object(
            optimization_store, "link_optimization_to_bot", lambda *a, **k: True,
        ):
            bot = {
                "symbol": "ETHUSDT",
                "strategy": "MACD_RSI",
                "status": "PAUSED",
                "config": {"rsi_period": 14},
            }
            req, _ = _fake_request("b1", {"optimization_run_id": "r1"}, bot)
            resp = asyncio.run(app_module.apply_optimized_config_handler(req))

        payload = json.loads(resp.body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(
            payload["config_diff"], {"rsi_period": {"from": 14, "to": 21}},
        )
