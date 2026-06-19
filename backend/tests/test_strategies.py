"""Unit tests for bot strategy framework."""

import unittest

from app.services.bots.indicators import (
    first_eval_index,
    macd_hist_col,
    merge_strategy_config,
    prepare_strategy_df,
    rsi_col,
)
from app.services.bots.screener import MarketScreenerService
from app.services.bots.strategies import (
    BrsScalpingStrategy,
    MacdRsiStrategy,
    SupertrendAdxStrategy,
    VwapPullbackStrategy,
    get_strategy,
)
from app.services.bots.backtester import BacktesterService


def make_candles(count: int = 120, start: float = 100.0, drift: float = 0.0005) -> list:
    candles = []
    base_time = 1_700_000_000
    price = start
    for i in range(count):
        o = price
        h = price * 1.003
        l = price * 0.997
        c = price * (1 + drift)
        candles.append(
            {
                "time": base_time + i * 60,
                "open": round(o, 4),
                "high": round(h, 4),
                "low": round(l, 4),
                "close": round(c, 4),
                "volume": 10_000,
            }
        )
        price = c
    return candles


class TestIndicatorConfig(unittest.TestCase):
    def test_merge_defaults(self):
        cfg = merge_strategy_config("MACD_RSI", {"rsi_length": 10})
        self.assertEqual(cfg["rsi_length"], 10)
        self.assertEqual(cfg["macd_fast"], 12)

    def test_column_names(self):
        self.assertEqual(macd_hist_col(12, 26, 9), "MACDh_12_26_9")
        self.assertEqual(rsi_col(14), "RSI_14")

    def test_first_eval_index(self):
        self.assertGreaterEqual(first_eval_index(None, "MACD_RSI", {}), 49)


class TestStrategyEvaluate(unittest.TestCase):
    def test_macd_buy_on_crossover(self):
        cfg = merge_strategy_config("MACD_RSI", {})
        hist = macd_hist_col(cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"])
        row = {
            hist: 0.5,
            f"{hist}_prev": -0.1,
            rsi_col(cfg["rsi_length"]): 40,
            "close": 100,
        }
        result = MacdRsiStrategy(cfg).evaluate(row)
        self.assertEqual(result["signal"], "BUY")

    def test_macd_no_signal_when_rsi_blocks(self):
        cfg = merge_strategy_config("MACD_RSI", {})
        hist = macd_hist_col(cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"])
        row = {
            hist: 0.5,
            f"{hist}_prev": -0.1,
            rsi_col(cfg["rsi_length"]): 60,
            "close": 100,
        }
        result = MacdRsiStrategy(cfg).evaluate(row)
        self.assertEqual(result["signal"], "NONE")

    def test_vwap_pullback_buy(self):
        cfg = merge_strategy_config("VWAP_PULLBACK", {})
        row = {"VWAP": 100, "close": 99.5, "close_prev": 100.5, "ATR_14": 1.0}
        result = VwapPullbackStrategy(cfg).evaluate(row)
        self.assertEqual(result["signal"], "BUY")

    def test_get_strategy_aliases(self):
        self.assertIsInstance(get_strategy("SUPERTREND", {}), SupertrendAdxStrategy)
        self.assertIsInstance(get_strategy("BB_STOCH", {}), BrsScalpingStrategy)


class TestScreenerAndBacktest(unittest.TestCase):
    def setUp(self):
        self.candles = make_candles()
        self.screener = MarketScreenerService()

    def test_screener_custom_rsi_length(self):
        config = {"rsi_length": 10, "macd_fast": 8, "macd_slow": 21, "macd_signal": 5}
        df = self.screener.process_candles("TEST", self.candles, config, "MACD_RSI")
        self.assertIn(rsi_col(10), df.columns)
        self.assertIn(macd_hist_col(8, 21, 5), df.columns)

    def test_prepare_strategy_df_adds_prev(self):
        config = merge_strategy_config("MACD_RSI", {})
        df = self.screener.process_candles("TEST", self.candles, config, "MACD_RSI")
        prepared = prepare_strategy_df(df, "MACD_RSI", config)
        hist = macd_hist_col(12, 26, 9)
        self.assertIn(f"{hist}_prev", prepared.columns)

    def test_backtest_runs(self):
        backtester = BacktesterService(self.screener)
        result = backtester.run_backtest(
            "TEST",
            "MACD_RSI",
            {"rsi_length": 14},
            self.candles,
        )
        self.assertNotIn("error", result)
        self.assertIn("win_rate", result)
        self.assertIn("equity_curve", result)
        self.assertIn("summary", result)


if __name__ == "__main__":
    unittest.main()
