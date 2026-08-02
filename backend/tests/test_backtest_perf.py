import unittest

from app.services.bots.backtest_payload import (
    MAX_PERSIST_EQUITY_POINTS,
    MAX_PERSIST_TRADES,
    trim_results_for_persist,
    trim_results_for_wire,
)
from app.services.bots.backtest_perf import (
    heavy_backtest_label,
    is_heavy_backtest,
    parallel_worker_count,
)


class TestBacktestPerf(unittest.TestCase):
    def test_parallel_worker_count_bounds(self):
        from app.config import BACKTEST_PARALLEL_MAX

        self.assertEqual(parallel_worker_count(0), 1)
        self.assertEqual(parallel_worker_count(1), 1)
        self.assertGreaterEqual(parallel_worker_count(20), 2)
        self.assertLessEqual(parallel_worker_count(20), max(8, int(BACKTEST_PARALLEL_MAX)))

    def test_parallel_backend_auto_is_process_or_thread(self):
        from app.services.bots.backtest_perf import parallel_backend

        self.assertIn(parallel_backend(), ("process", "thread"))

    def test_parallel_backend_auto_process_unless_cuda_session_loaded(self):
        """auto must not force threads merely because CUDA EP is installed."""
        from unittest.mock import patch

        from app.config import BACKTEST_PARALLEL_BACKEND
        from app.services.bots import backtest_perf as perf
        from app.services.bots import ml_onnx_runtime as ort_mod

        if (BACKTEST_PARALLEL_BACKEND or "auto").strip().lower() not in (
            "auto", "", "default",
        ):
            self.skipTest("BACKTEST_PARALLEL_BACKEND overridden")

        prev = ort_mod._cuda_session_created
        try:
            ort_mod._cuda_session_created = False
            with patch.object(ort_mod, "cuda_ep_available", return_value=True):
                self.assertEqual(perf.parallel_backend(), "process")
            ort_mod._cuda_session_created = True
            self.assertEqual(perf.parallel_backend(), "thread")
        finally:
            ort_mod._cuda_session_created = prev

    def test_is_heavy_portfolio(self):
        self.assertTrue(
            is_heavy_backtest(portfolio_symbols=["BTCUSDT", "ETHUSDT"]),
        )
        self.assertFalse(
            is_heavy_backtest(portfolio_symbols=["BTCUSDT"]),
        )

    def test_is_heavy_sweep_and_long_range(self):
        self.assertTrue(is_heavy_backtest(sweep={"trailing_stop_percent": [1, 2]}))
        self.assertTrue(is_heavy_backtest(days=30))
        self.assertTrue(is_heavy_backtest(walk_forward=True, days=7))
        self.assertFalse(is_heavy_backtest(days=7))

    def test_heavy_backtest_label(self):
        self.assertEqual(
            heavy_backtest_label({"portfolio_symbols": ["A", "B"]}),
            "portfolio",
        )
        self.assertEqual(
            heavy_backtest_label({"sweep": {"x": [1]}}),
            "sweep",
        )

    def test_sweep_combo_count_uses_trial_budget(self):
        from app.services.bots.backtest_perf import _sweep_combo_count

        n = _sweep_combo_count({"sweep_mode": "random", "max_combos": 150})
        self.assertLessEqual(n, 200)

    def test_trim_results_for_persist_downsamples(self):
        raw = {
            "equity_curve": [{"time": i, "equity": i} for i in range(5000)],
            "trades": [
                {"pnl": 1, "insight_snapshot": {"signal": "BUY"}},
                {"pnl": 2},
            ],
            "trades_total": 5000,
        }
        out = trim_results_for_persist(raw)
        self.assertLessEqual(len(out["equity_curve"]), MAX_PERSIST_EQUITY_POINTS)
        self.assertEqual(out["trades_total"], 5000)
        self.assertIn("insight_snapshot", out["trades"][0])

    def test_trim_results_for_wire_keeps_insight_snapshot(self):
        raw = {
            "trades": [{"pnl": 1, "insight_snapshot": {"x": 1}}],
        }
        out = trim_results_for_wire(raw)
        self.assertIn("insight_snapshot", out["trades"][0])

    def test_trim_persist_caps_trades(self):
        raw = {
            "trades": [{"pnl": i} for i in range(MAX_PERSIST_TRADES + 50)],
        }
        out = trim_results_for_persist(raw)
        self.assertEqual(len(out["trades"]), MAX_PERSIST_TRADES)


if __name__ == "__main__":
    unittest.main()
