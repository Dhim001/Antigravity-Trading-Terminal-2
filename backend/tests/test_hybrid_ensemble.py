"""Tests for HYBRID_ENSEMBLE weighted TA + ML + RL voting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class _StubStrategy:
    def __init__(self, signal: str, confidence: float = 0.8):
        self._signal = signal
        self._confidence = confidence

    def evaluate(self, df_row):
        return {"signal": self._signal, "confidence": self._confidence}


def _make_ensemble(ta, ml, rl, **cfg_extra):
    from app.services.bots.strategies_ensemble import HybridEnsembleStrategy

    cfg = {
        "ta_strategy": "MACD_RSI",
        "ml_strategy": "ML_SIGNAL_BOOST",
        "rl_strategy": "RL_PPO_AGENT",
        "ensemble_weight_ta": 0.3,
        "ensemble_weight_ml": 0.4,
        "ensemble_weight_rl": 0.3,
        "ensemble_threshold": 0.5,
        "calibration_gate_enabled": False,
        **cfg_extra,
    }
    with patch(
        "app.services.bots.strategies_ensemble._safe_get_strategy",
        side_effect=lambda name, config: {
            "MACD_RSI": ta,
            "ML_SIGNAL_BOOST": ml,
            "RL_PPO_AGENT": rl,
        }.get(str(name).upper()),
    ):
        return HybridEnsembleStrategy(cfg)


class TestHybridEnsembleVote:
    def test_fires_when_weighted_vote_clears_threshold(self):
        ens = _make_ensemble(
            _StubStrategy("BUY", 0.9),
            _StubStrategy("BUY", 0.9),
            _StubStrategy("NONE", 0.1),
            ensemble_threshold=0.4,
        )
        out = ens.evaluate({"ATR_14": 1.0, "close": 100})
        assert out["signal"] == "BUY"
        assert out["confidence"] > 0.4
        assert out["model_type"] == "hybrid_ensemble"
        assert "ensemble" in out

    def test_none_when_below_threshold(self):
        ens = _make_ensemble(
            _StubStrategy("BUY", 0.2),
            _StubStrategy("SELL", 0.2),
            _StubStrategy("NONE", 0.1),
            ensemble_threshold=0.9,
        )
        out = ens.evaluate({"close": 100})
        assert out["signal"] == "NONE"
        assert out.get("reject_reason") in ("ensemble_below_threshold", "ensemble_none")

    def test_require_agreement_blocks_single_leg(self):
        ens = _make_ensemble(
            _StubStrategy("BUY", 1.0),
            _StubStrategy("NONE", 0.0),
            _StubStrategy("NONE", 0.0),
            ensemble_weight_ta=1.0,
            ensemble_weight_ml=0.0,
            ensemble_weight_rl=0.0,
            ensemble_threshold=0.1,
            ensemble_require_agreement=True,
        )
        out = ens.evaluate({"close": 100})
        assert out["signal"] == "NONE"
        assert out.get("reject_reason") == "ensemble_no_agreement"

    def test_require_agreement_allows_two_legs(self):
        ens = _make_ensemble(
            _StubStrategy("SELL", 0.9),
            _StubStrategy("SELL", 0.9),
            _StubStrategy("NONE", 0.0),
            ensemble_threshold=0.3,
            ensemble_require_agreement=True,
        )
        out = ens.evaluate({"close": 100})
        assert out["signal"] == "SELL"

    def test_adaptive_weights_override_static(self):
        from app.services.bots.strategies_ensemble import _resolve_weights

        ta, ml, rl = _resolve_weights({
            "ensemble_weight_ta": 0.3,
            "ensemble_weight_ml": 0.4,
            "ensemble_weight_rl": 0.3,
            "ensemble_adaptive_weights": {"ta": 0.1, "ml": 0.8, "rl": 0.1},
        })
        assert ml == pytest.approx(0.8)
        assert ta == pytest.approx(0.1)

    def test_get_strategy_registers_hybrid(self):
        from app.services.bots.strategies import get_strategy

        with patch(
            "app.services.bots.strategies_ensemble._safe_get_strategy",
            return_value=_StubStrategy("NONE", 0.0),
        ):
            s = get_strategy("HYBRID_ENSEMBLE", {"ensemble_threshold": 0.99})
        assert s is not None
        out = s.evaluate({"close": 1})
        assert out["signal"] == "NONE"


class TestDeployGateEnsemble:
    def test_blocks_missing_component_models(self):
        from app.services.bots.deploy_gate import evaluate_deploy_gate

        results = {"total_pnl": 100, "trade_count": 20, "summary": {"total_pnl": 100, "total_trades": 20}}

        def age(strategy, symbol):
            return None

        with patch("app.services.bots.ml_retrain_scheduler.get_model_age_hours", side_effect=age), \
             patch("app.services.bots.ml_retrain_scheduler.get_model_metadata", return_value={}):
            gate = evaluate_deploy_gate(
                results,
                symbol="BTCUSDT",
                run_config={"strategy": "HYBRID_ENSEMBLE"},
            )
        ml_leg = [c for c in gate["checks"] if c["id"] == "ensemble_ml_model"]
        assert ml_leg and ml_leg[0]["level"] == "block"

    def test_passes_when_components_validated(self):
        from app.services.bots.deploy_gate import evaluate_deploy_gate

        results = {"total_pnl": 100, "trade_count": 20, "summary": {"total_pnl": 100, "total_trades": 20}}
        meta = {
            "validated_at": "2026-07-01T00:00:00Z",
            "walk_forward": {
                "ok": True,
                "recommendation": "DEPLOY",
                "mean_oos_accuracy": 0.55,
            },
            "pbo": 0.2,
        }

        with patch("app.services.bots.ml_retrain_scheduler.get_model_age_hours", return_value=12.0), \
             patch("app.services.bots.ml_retrain_scheduler.get_model_metadata", return_value=meta):
            gate = evaluate_deploy_gate(
                results,
                symbol="BTCUSDT",
                run_config={"strategy": "HYBRID_ENSEMBLE"},
            )
        assert not any(
            c["level"] == "block" and c["id"].startswith("ensemble_")
            for c in gate["checks"]
        )
        wf = [c for c in gate["checks"] if c["id"] == "ml_walk_forward"]
        assert wf and wf[0]["ok"] is True
