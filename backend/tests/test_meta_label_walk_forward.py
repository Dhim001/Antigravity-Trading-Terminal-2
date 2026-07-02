"""Walk-forward meta-label backtest evaluation tests."""

from __future__ import annotations

import copy
import unittest

from app.services.bots.meta_label_walk_forward import (
    closed_rows_from_backtest_trades,
    evaluate_meta_label_walk_forward,
    _aggregate_fold_results,
)
from app.services.bots.meta_label_model import train_model_from_rows, insight_to_features


def _snap(score: int = 3, conf: float = 0.75):
    return {
        "score": score,
        "confidence": conf,
        "sub_reports": {
            "trend": {"score": 1, "trend_regime": "trending"},
            "momentum": {"score": 1},
            "volume": {"score": 0},
            "sentiment": {"score": 0, "aggregate_score": 0.1},
            "risk": {"atr_regime": "normal", "suggested_size_factor": 1.0},
        },
    }


class ClosedRowsFromBacktestTests(unittest.TestCase):
    def test_pairs_entry_exit_with_snapshot(self):
        trades = [
            {"time": 100, "side": "BUY", "is_exit": False, "insight_snapshot": _snap()},
            {"time": 200, "side": "SELL", "is_exit": True, "pnl": 10.0},
            {"time": 300, "side": "BUY", "is_exit": False, "insight_snapshot": _snap(2, 0.6)},
            {"time": 400, "side": "SELL", "is_exit": True, "pnl": -5.0},
        ]
        rows = closed_rows_from_backtest_trades(trades, symbol="AAPL", timeframe="1m")
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["win"])
        self.assertFalse(rows[1]["win"])


class WalkForwardAggregateTests(unittest.TestCase):
    def test_recommends_gbm_when_pnl_improves(self):
        folds = [{
            "gbm_oos": {"total_pnl": 100, "expectancy": 2},
            "baseline_oos": {"total_pnl": 50, "expectancy": 1},
            "gbm_vs_baseline": {"total_pnl": 50, "expectancy": 1},
        }]
        agg = _aggregate_fold_results(folds, min_train_samples=10)
        self.assertTrue(agg.get("ok"))
        self.assertIn("improved", agg.get("recommendation", "").lower())


class WalkForwardEvaluatorTests(unittest.TestCase):
    def test_evaluate_with_mock_backtest(self):
        call = {"n": 0}
        candles = [{"time": i, "open": 100, "high": 101, "low": 99, "close": 100 + (i % 5), "volume": 1000} for i in range(220)]

        def fake_run(symbol, strategy, config, cands):
            call["n"] += 1
            gate_on = bool(config.get("calibration_gate_enabled"))
            n_trades = 12 if len(cands) > 100 else 8
            trades = []
            for j in range(n_trades):
                t0 = j * 2
                win = (j + call["n"]) % 3 != 0
                trades.append({
                    "time": t0,
                    "side": "BUY",
                    "is_exit": False,
                    "insight_snapshot": _snap(3 if win else 2, 0.8 if win else 0.55),
                })
                trades.append({
                    "time": t0 + 1,
                    "side": "SELL",
                    "is_exit": True,
                    "pnl": 5.0 if win else -3.0,
                })
            blocked = 3 if gate_on else 0
            executed = max(0, n_trades - blocked)
            pnl = executed * 1.5 if gate_on else n_trades * 0.5
            return {
                "trades": trades,
                "trade_count": executed,
                "total_pnl": pnl,
                "win_rate": 55.0,
                "max_drawdown": 2.0,
                "summary": {
                    "total_pnl": pnl,
                    "win_rate": 55.0,
                    "total_trades": executed,
                    "max_drawdown": 2.0,
                    "expectancy": pnl / max(executed, 1),
                    "filter_rejects": {"calibration": blocked} if gate_on else {},
                    "blocked_entries": blocked,
                },
            }

        out = evaluate_meta_label_walk_forward(
            fake_run,
            "AAPL",
            "CHART_AGENT",
            {"allocation": 5000, "timeframe": "1m"},
            candles,
            rolling_folds=1,
            train_pct=70.0,
            min_train_samples=10,
        )
        self.assertTrue(out.get("ok"), out.get("error"))
        self.assertGreaterEqual(len(out.get("folds") or []), 1)
        self.assertIn("aggregate", out)
        self.assertGreater(call["n"], 2)


if __name__ == "__main__":
    unittest.main()
