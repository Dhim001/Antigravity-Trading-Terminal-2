"""Backtest replay applies CHART_AGENT meta-label calibration gate."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.agent.models import ChartAgentInsight
from app.services.bots.backtester import BacktesterService
from app.services.bots.screener import MarketScreenerService
from tests.test_chart_agent_rules import make_trending_candles


def _buy_insight(symbol: str, bar_time: int) -> ChartAgentInsight:
    return ChartAgentInsight(
        symbol=symbol,
        bar_time=int(bar_time),
        timeframe="1m",
        signal="BUY",
        confidence=0.8,
        score=3,
        reasons=["trend up"],
        sub_reports={
            "trend": {"score": 1},
            "momentum": {"score": 1},
            "risk": {"score": 0, "atr_regime": "normal", "suggested_size_factor": 1.0},
        },
        levels={"stop_loss_distance": 50.0},
    )


class TestBacktestCalibrationGate(unittest.TestCase):
    def test_calibration_gate_blocks_entries_in_backtest(self):
        candles = make_trending_candles(220, drift=0.001)
        screener = MarketScreenerService()
        backtester = BacktesterService(screener)

        cfg = {
            "allocation": 5000,
            "timeframe": "1m",
            "backtest_bot_id": "bt-cal-gate",
            "calibration_gate_enabled": True,
            "calibration_min_samples": 3,
            "calibration_min_wilson": 0.99,
            "min_confidence": 0.4,
            "min_score": 1,
        }

        def fake_score(df, i, symbol):
            bar_time = df.iloc[i].get("time") or i
            return _buy_insight(symbol, bar_time)

        with patch("app.services.agent.rule_engine.score_at_index", side_effect=fake_score), patch(
            "app.services.bots.calibration.check_meta_label_gate",
            return_value="calibration gate: setup Wilson lower 10.00% below 99.00% (n=10)",
        ):
            result = backtester.run_backtest("BTCUSDT", "CHART_AGENT", cfg, candles)

        self.assertNotIn("error", result)
        summary = result.get("summary") or {}
        filter_rejects = summary.get("filter_rejects") or {}
        self.assertGreater(filter_rejects.get("calibration", 0), 0)
        self.assertEqual(summary.get("total_trades", 0), 0)


if __name__ == "__main__":
    unittest.main()
