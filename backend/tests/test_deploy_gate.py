"""Tests for deploy gate — forward test before capital."""

from app.services.bots.deploy_gate import (
    config_fingerprint,
    enrich_deploy_config,
    evaluate_deploy_gate,
)


def _standard_results(*, pnl=100.0, trades=5, oos_pct=None):
    meta = {"oos_pct": oos_pct} if oos_pct else {}
    return {
        "total_pnl": pnl,
        "trade_count": trades,
        "summary": {"total_pnl": pnl, "total_trades": trades},
        "meta": meta,
    }


def _wf_results(*, oos_pnl=50.0, oos_trades=3, stability=0.8, fold_count=4):
    return {
        "walk_forward": {
            "out_of_sample": {"total_pnl": oos_pnl, "trade_count": oos_trades},
            "aggregate": {"stability_score": stability, "fold_count": fold_count},
        },
        "summary": {"total_trades": oos_trades},
    }


def test_no_results_warn_only():
    gate = evaluate_deploy_gate(None)
    assert gate["passed"] is True
    assert gate["blocking"] is False
    assert gate["workflow_stage"] == "backtest"
    assert any(c["id"] == "backtest_linked" for c in gate["checks"])


def test_standard_backtest_passes():
    gate = evaluate_deploy_gate(_standard_results())
    assert gate["passed"] is True
    assert gate["blocking"] is False


def test_standard_backtest_blocks_negative_pnl():
    gate = evaluate_deploy_gate(_standard_results(pnl=-10.0))
    assert gate["passed"] is False
    assert gate["blocking"] is True
    assert "below minimum" in gate["block_reason"]


def test_standard_backtest_blocks_low_trades():
    gate = evaluate_deploy_gate(_standard_results(trades=0))
    assert gate["passed"] is False
    assert any(c["id"] == "trade_count" and not c["ok"] for c in gate["checks"])


def test_walk_forward_passes():
    gate = evaluate_deploy_gate(_wf_results())
    assert gate["passed"] is True
    assert gate["workflow_stage"] == "oos_validated"


def test_walk_forward_blocks_negative_oos():
    gate = evaluate_deploy_gate(_wf_results(oos_pnl=-5.0))
    assert gate["passed"] is False
    assert gate["blocking"] is True


def test_walk_forward_blocks_low_stability():
    gate = evaluate_deploy_gate(
        _wf_results(stability=0.2, fold_count=4),
        min_stability_score=0.5,
    )
    assert gate["passed"] is False


def test_portfolio_symbol_slice_blocks_error_row():
    results = {
        "portfolio": True,
        "symbol_results": [
            {"symbol": "AAPL", "error": "no data"},
            {"symbol": "MSFT", "total_pnl": 20, "trade_count": 2},
        ],
    }
    gate = evaluate_deploy_gate(results, symbol="AAPL")
    assert gate["blocking"] is True
    assert "failed for AAPL" in gate["block_reason"]


def test_portfolio_symbol_slice_uses_row_metrics():
    results = {
        "portfolio": True,
        "total_pnl": -100,
        "trade_count": 0,
        "symbol_results": [
            {"symbol": "MSFT", "total_pnl": 50, "trade_count": 4, "summary": {}},
        ],
    }
    gate = evaluate_deploy_gate(results, symbol="MSFT")
    assert gate["passed"] is True


def test_config_fingerprint_stable():
    fp1 = config_fingerprint(symbol="AAPL", strategy="CHART_AGENT", days=7, timeframe="1m", config={"allocation": 1000})
    fp2 = config_fingerprint(symbol="AAPL", strategy="CHART_AGENT", days=7, timeframe="1m", config={"allocation": 1000})
    assert fp1 == fp2


def test_fingerprint_mismatch_warns():
    results = _standard_results()
    run_cfg = {"allocation": 1000}
    gate = evaluate_deploy_gate(
        results,
        deploy_fingerprint=config_fingerprint(
            symbol="AAPL", strategy="X", days=7, timeframe="1m", config={"allocation": 2000},
        ),
        run_config=run_cfg,
        run_days=7,
    )
    assert any(c["id"] == "config_fingerprint" for c in gate["checks"])


def test_enrich_deploy_config_persists_audit_fields():
    out = enrich_deploy_config(
        {"min_confidence": 0.6},
        run_id="run-1",
        fingerprint='{"symbol":"AAPL"}',
        gate={"passed": True},
    )
    assert out["backtest_run_id"] == "run-1"
    assert out["backtest_fingerprint"] == '{"symbol":"AAPL"}'
    assert "deploy_gate_passed_at" in out
    assert out["deploy_workflow"] == "backtest→oos→deploy"
