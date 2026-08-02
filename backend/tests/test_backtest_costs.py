"""Tests for backtest slippage/fees impact."""

import unittest
from unittest.mock import patch

from app.services.bots.backtester import BacktesterService
from app.services.bots.screener import MarketScreenerService
from app.services.bots.strategies import BaseStrategy


def _make_candles(count: int = 120, start: float = 100.0) -> list:
    candles = []
    base_time = 1_700_000_000
    price = start
    for i in range(count):
        candles.append({
            "time": base_time + i * 60,
            "open": round(price, 4),
            "high": round(price * 1.01, 4),
            "low": round(price * 0.99, 4),
            "close": round(price, 4),
            "volume": 10_000,
        })
    return candles


class AlwaysBuyStrategy(BaseStrategy):
    def evaluate(self, df_row) -> dict:
        return {"signal": "BUY", "stop_loss_distance": 2.0}


class BacktestCostsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.backtester = BacktesterService(MarketScreenerService())
        self.candles = _make_candles(120)
        import app.services.bots.backtester as bt_mod
        self._orig_get = bt_mod.get_strategy
        bt_mod.get_strategy = lambda _n, _c: AlwaysBuyStrategy({})
        self._gate_patcher = patch(
            "app.services.altdata.event_policy.check_entry_gates",
            return_value=(True, None, None),
        )
        self._gate_patcher.start()

    def tearDown(self):
        import app.services.bots.backtester as bt_mod
        bt_mod.get_strategy = self._orig_get
        self._gate_patcher.stop()

    def test_fees_reduce_pnl_vs_zero_cost(self):
        base = self.backtester.run_backtest(
            "TEST", "MACD_RSI", {"allocation": 5000}, self.candles,
        )
        costly = self.backtester.run_backtest(
            "TEST",
            "MACD_RSI",
            {"allocation": 5000, "slippage_bps": 5, "fee_bps": 10},
            self.candles,
        )
        self.assertNotIn("error", base)
        self.assertNotIn("error", costly)
        self.assertGreaterEqual(base["total_pnl"], costly["total_pnl"])
        self.assertGreater(costly["summary"]["total_fees"], 0)

    def test_research_fast_columnar_uses_bar_volume_for_participation(self):
        """Regression: research_fast slim row must include volume (not treat as 0)."""
        high_vol = _make_candles(160)
        low_vol = []
        for c in high_vol:
            low_vol.append({**c, "volume": 1.0})

        def _buy_precompute(strategy, df, start_i, **_kwargs):
            n = len(df) - int(start_i)
            return [
                {"signal": "BUY", "stop_loss_distance": 2.0} if (i % 40 == 0) else {"signal": "NONE"}
                for i in range(n)
            ]

        cfg = {
            "allocation": 5000,
            "sim_mode": "research_fast",
            "volume_participation": True,
            "slippage_bps": 20,
            "fee_bps": 0,
            "batch_inference": True,
        }
        with patch(
            "app.services.bots.ml_batch_inference.try_precompute_signals_from_df",
            side_effect=_buy_precompute,
        ):
            hi = self.backtester.run_backtest("TEST", "ML_SIGNAL_BOOST", cfg, high_vol)
            lo = self.backtester.run_backtest("TEST", "ML_SIGNAL_BOOST", cfg, low_vol)

        self.assertNotIn("error", hi)
        self.assertNotIn("error", lo)
        self.assertGreater(hi.get("trade_count") or 0, 0)
        self.assertGreater(lo.get("trade_count") or 0, 0)
        # Near-zero bar volume → max participation impact → worse (more negative) PnL
        # than deep-liquidity bars with the same signals.
        self.assertLess(lo["total_pnl"], hi["total_pnl"])


if __name__ == "__main__":
    unittest.main()
