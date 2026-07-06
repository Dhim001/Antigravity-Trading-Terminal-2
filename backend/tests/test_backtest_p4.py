"""Tests for backtest P4+ — analytics, walk-forward, research sim_mode."""

import unittest
from unittest.mock import patch

from app.services.bots.backtest_analytics import (
    buy_and_hold_benchmark,
    buy_and_hold_equity_curve,
    classify_backtest_regime,
    drawdown_curve,
    enrich_summary,
    sortino_ratio,
    align_benchmark_equity_curve,
)
from app.services.bots.backtest_walk_forward import (
    aggregate_fold_oos,
    build_rolling_fold_windows,
    pick_best_config,
    sort_sweep_rows,
    split_train_test,
)
from app.services.bots.backtester import BacktesterService
from app.services.bots.screener import MarketScreenerService
from tests.test_backtest_align import _make_candles


class TestBacktestAnalytics(unittest.TestCase):
    def test_sortino_from_equity_curve(self):
        curve = [
            {"time": 1, "equity": 1000},
            {"time": 2, "equity": 1010},
            {"time": 3, "equity": 1005},
            {"time": 4, "equity": 1020},
        ]
        ratio = sortino_ratio(curve)
        self.assertIsNotNone(ratio)

    def test_buy_and_hold_benchmark(self):
        candles = _make_candles(10, start=100.0, drift=0.001)
        bench = buy_and_hold_benchmark(candles, 1000.0)
        self.assertIsNotNone(bench)
        self.assertIn("pnl", bench)
        self.assertIn("return_pct", bench)

    def test_drawdown_curve(self):
        curve = [
            {"time": 1, "equity": 1000},
            {"time": 2, "equity": 1100},
            {"time": 3, "equity": 990},
        ]
        dd = drawdown_curve(curve)
        self.assertEqual(len(dd), 3)
        self.assertEqual(dd[0]["drawdown_pct"], 0.0)
        self.assertGreater(dd[2]["drawdown_pct"], 0)

    def test_enrich_summary_adds_alpha(self):
        candles = _make_candles(20, start=100.0, drift=0.002)
        curve = [{"time": c["time"], "equity": 1000 + i * 2} for i, c in enumerate(candles)]
        summary = enrich_summary(
            {"total_pnl": 40.0, "return_pct": 4.0},
            equity_curve=curve,
            candles=candles,
            starting_equity=1000.0,
        )
        self.assertIn("sortino_ratio", summary)
        self.assertIn("benchmark", summary)
        self.assertIn("alpha_pnl", summary)
        self.assertIn("alpha_return_pct", summary)

    def test_classify_backtest_regime(self):
        candles = _make_candles(80, start=100.0, drift=0.002)
        regime = classify_backtest_regime(candles)
        self.assertIn(regime["dominant_regime"], ("elevated", "normal", "compressed", "unknown"))
        self.assertIn("breakdown_pct", regime)

    def test_buy_and_hold_equity_curve(self):
        candles = _make_candles(10, start=100.0, drift=0.001)
        curve = buy_and_hold_equity_curve(candles, 1000.0)
        self.assertEqual(len(curve), len(candles))
        self.assertGreater(curve[-1]["equity"], 0)

    def test_align_benchmark_equity_curve(self):
        bench = [{"time": i * 3600, "close": 100 + i} for i in range(10)]
        equity = [{"time": i * 3600, "equity": 1000 + i * 5} for i in range(10)]
        aligned = align_benchmark_equity_curve(bench, equity, 1000.0)
        self.assertEqual(len(aligned), 10)

    def test_enrich_summary_includes_regime(self):
        candles = _make_candles(80, start=100.0, drift=0.002)
        curve = [{"time": c["time"], "equity": 1000 + i * 2} for i, c in enumerate(candles)]
        summary = enrich_summary(
            {"total_pnl": 40.0, "return_pct": 4.0},
            equity_curve=curve,
            candles=candles,
            starting_equity=1000.0,
            symbol="TEST",
        )
        self.assertIn("regime", summary)
        self.assertIn("benchmark_overlays", summary)


class TestWalkForward(unittest.TestCase):
    def test_split_train_test(self):
        candles = _make_candles(200)
        meta = {"symbol": "TEST", "oldest": candles[0]["time"], "newest": candles[-1]["time"]}
        train, test, train_meta, test_meta = split_train_test(candles, meta, 70.0)
        self.assertEqual(len(train) + len(test), len(candles))
        self.assertGreaterEqual(len(train), 50)
        self.assertGreaterEqual(len(test), 50)
        self.assertEqual(train_meta.get("window"), "in_sample")
        self.assertEqual(test_meta.get("window"), "out_of_sample")

    def test_pick_best_config(self):
        rows = [
            {"config": {"a": 1}, "total_pnl": 10, "trade_count": 5},
            {"config": {"a": 2}, "total_pnl": 25, "summary": {"total_pnl": 25}, "trade_count": 3},
            {"config": {"a": 3}, "error": "fail"},
        ]
        cfg, row = pick_best_config(rows)
        self.assertEqual(cfg, {"a": 2})
        self.assertEqual(row["total_pnl"], 25)

    def test_pick_best_config_sharpe_objective(self):
        rows = [
            {"config": {"a": 1}, "total_pnl": 50, "summary": {"sharpe_ratio": 0.5}, "trade_count": 10},
            {"config": {"a": 2}, "total_pnl": 10, "summary": {"sharpe_ratio": 1.8}, "trade_count": 8},
        ]
        cfg, row = pick_best_config(rows, objective="sharpe_ratio")
        self.assertEqual(cfg, {"a": 2})

    def test_pick_best_config_min_trades(self):
        rows = [
            {"config": {"a": 1}, "total_pnl": 100, "trade_count": 1},
            {"config": {"a": 2}, "total_pnl": 20, "trade_count": 5},
        ]
        cfg, row = pick_best_config(rows, min_trades=3)
        self.assertEqual(cfg, {"a": 2})
        ranked = sort_sweep_rows(rows, min_trades=3)
        self.assertEqual(len(ranked), 1)

    def test_build_rolling_fold_windows_single(self):
        candles = _make_candles(200)
        meta = {"symbol": "TEST"}
        windows = build_rolling_fold_windows(candles, meta, rolling_folds=1)
        self.assertEqual(len(windows), 1)
        train, test, _, _ = windows[0]
        self.assertEqual(len(train) + len(test), len(candles))

    def test_build_rolling_fold_windows_multi(self):
        candles = _make_candles(500)
        meta = {"symbol": "TEST"}
        windows = build_rolling_fold_windows(candles, meta, rolling_folds=3)
        self.assertEqual(len(windows), 3)
        for train, test, train_meta, test_meta in windows:
            self.assertGreaterEqual(len(train), 50)
            self.assertGreaterEqual(len(test), 50)
            self.assertEqual(train_meta.get("window"), "in_sample")
            self.assertEqual(test_meta.get("window"), "out_of_sample")

    def test_aggregate_fold_oos(self):
        folds = [
            {"out_of_sample": {"total_pnl": 10, "summary": {"sharpe_ratio": 1.0}}},
            {"out_of_sample": {"total_pnl": -5, "summary": {"sharpe_ratio": 0.5}}},
            {"out_of_sample": {"total_pnl": 20, "summary": {"sharpe_ratio": 1.5}}},
        ]
        agg = aggregate_fold_oos(folds)
        self.assertEqual(agg["fold_count"], 3)
        self.assertAlmostEqual(agg["mean_pnl"], 25 / 3, places=2)
        self.assertAlmostEqual(agg["stability_score"], 2 / 3, places=2)

    def test_rolling_walk_forward_mock(self):
        from unittest.mock import MagicMock
        from app.services.bots.backtest_walk_forward import run_walk_forward

        candles = _make_candles(500)
        meta = {"symbol": "TEST"}
        configs = [{"a": 1}, {"a": 2}]

        call_count = {"n": 0}

        def mock_backtest(symbol, strategy, cfg, bars, progress_cb=None, cancel_cb=None):
            call_count["n"] += 1
            pnl = float(cfg.get("a", 0)) * 10
            return {
                "summary": {"total_pnl": pnl, "sharpe_ratio": pnl / 10, "total_trades": 5},
                "total_pnl": pnl,
                "trade_count": 5,
            }

        result = run_walk_forward(
            run_backtest=mock_backtest,
            symbol="TEST",
            strategy="MACD_RSI",
            base_config=configs[0],
            candles=candles,
            meta=meta,
            configs=configs,
            rolling_folds=3,
            min_trades=1,
        )
        self.assertNotIn("error", result)
        wf = result.get("walk_forward") or {}
        self.assertEqual(wf.get("rolling_folds"), 3)
        self.assertEqual(len(wf.get("folds") or []), 3)
        self.assertIn("aggregate", wf)
        self.assertIsNotNone(wf.get("best_config"))
        self.assertGreater(call_count["n"], 0)


class TestResearchSimMode(unittest.TestCase):
    def setUp(self):
        self.backtester = BacktesterService(MarketScreenerService())
        self.candles = _make_candles(120)
        self._gate_patcher = patch(
            "app.services.altdata.event_policy.check_entry_gates",
            return_value=(True, None, None),
        )
        self._gate_patcher.start()

    def tearDown(self):
        self._gate_patcher.stop()

    def test_research_mode_skips_risk_gates(self):
        live = self.backtester.run_backtest(
            "TEST",
            "MACD_RSI",
            {"allocation": 1000, "sim_mode": "live_aligned"},
            self.candles,
        )
        research = self.backtester.run_backtest(
            "TEST",
            "MACD_RSI",
            {"allocation": 1000, "sim_mode": "research"},
            self.candles,
        )
        self.assertNotIn("error", live)
        self.assertNotIn("error", research)
        self.assertEqual(live["sim_mode"], "live_aligned")
        self.assertEqual(research["sim_mode"], "research")
        self.assertIn("drawdown_curve", research)
        self.assertIn("sortino_ratio", research["summary"])
        self.assertIn("benchmark", research["summary"])


    def test_research_mode_allows_short_entries(self):
        from unittest.mock import MagicMock
        import app.services.bots.backtester as bt_mod

        class AlwaysShortStrategy:
            def evaluate(self, df_row):
                return {"signal": "SELL", "stop_loss_distance": 2.0}

        strategy = AlwaysShortStrategy()
        original_get = bt_mod.get_strategy
        bt_mod.get_strategy = lambda _name, _cfg: strategy
        self.backtester.screener.process_candles = MagicMock(
            return_value=self.backtester.screener.process_candles(
                "TEST", self.candles, {}, "MACD_RSI", full_history=True,
            )
        )
        try:
            result = self.backtester.run_backtest(
                "TEST",
                "MACD_RSI",
                {"allocation": 1000, "sim_mode": "research"},
                self.candles,
            )
        finally:
            bt_mod.get_strategy = original_get

        self.assertNotIn("error", result)
        short_entries = [
            t for t in result.get("trades", [])
            if not t.get("is_exit") and t.get("reason") == "ENTRY_SHORT"
        ]
        self.assertGreater(len(short_entries), 0)


if __name__ == "__main__":
    unittest.main()
