"""CHART_AGENT signal path on non-1m bot timeframes (5m / 15m / 4h)."""

from __future__ import annotations

import unittest

from app.services.bots.candle_source import candles_for_timeframe
from app.services.bots.strategies_chart_agent import build_signal_from_insight
from app.services.market.timeframes import normalize_timeframe


def _insight(tf: str, bar_time: int = 1_700_000_000):
    return {
        "symbol": "BTCUSDT",
        "bar_time": bar_time,
        "timeframe": tf,
        "signal": "BUY",
        "confidence": 0.72,
        "score": 3,
        "reasons": ["trend up"],
        "sub_reports": {
            "trend": {"score": 1, "reasons": ["trend up"]},
            "momentum": {"score": 1, "reasons": ["macd bullish"]},
            "risk": {"score": 0, "atr_regime": "normal", "suggested_size_factor": 1.0},
        },
        "levels": {"stop_loss_distance": 50.0},
        "insight_id": f"BTCUSDT:{tf}:{bar_time}",
    }


class TestChartAgentTimeframes(unittest.TestCase):
    def test_build_signal_respects_timeframe_label(self):
        for tf in ("5m", "15m", "4h"):
            cfg = {"symbol": "BTCUSDT", "timeframe": tf, "min_confidence": 0.55}
            out = build_signal_from_insight(
                _insight(tf),
                cfg,
                symbol="BTCUSDT",
                timeframe=tf,
            )
            self.assertEqual(out["signal"], "BUY", tf)
            snap = out.get("insight_snapshot") or {}
            self.assertEqual(normalize_timeframe(snap.get("timeframe", "1m")), tf)

    def test_resampled_candles_align_to_timeframe_buckets(self):
        base = (1_700_000_000 // 900) * 900
        one_min = [
            {
                "time": base + i * 60,
                "open": 100 + i * 0.01,
                "high": 101 + i * 0.01,
                "low": 99 + i * 0.01,
                "close": 100.5 + i * 0.01,
                "volume": 1.0,
            }
            for i in range(60)
        ]
        bars_15m = candles_for_timeframe(one_min, "15m")
        self.assertEqual(len(bars_15m), 4)
        self.assertEqual(bars_15m[0]["time"], base)
        self.assertEqual(bars_15m[-1]["time"], base + 3 * 900)


if __name__ == "__main__":
    unittest.main()
