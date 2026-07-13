"""Unit tests for bot strategy framework."""

import unittest

from app.services.bots.indicators import (
    atr_col,
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
            atr_col(cfg["atr_length"]): 1.0,
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
            atr_col(cfg["atr_length"]): 1.0,
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

    def test_brs_scalping_buy_sell_and_none(self):
        from app.services.bots.indicators import (
            atr_col,
            bb_lower_col,
            bb_mid_col,
            bb_upper_col,
            stoch_k_col,
        )

        cfg = merge_strategy_config("BRS_SCALPING", {})
        bbl = bb_lower_col(cfg["bb_length"], cfg["bb_std"])
        bbu = bb_upper_col(cfg["bb_length"], cfg["bb_std"])
        bbm = bb_mid_col(cfg["bb_length"], cfg["bb_std"])
        rsi = rsi_col(cfg["rsi_length"])
        stoch = stoch_k_col(cfg["stoch_k"], cfg["stoch_d"], cfg["stoch_smooth"])
        atr = atr_col(cfg["atr_length"])

        buy = BrsScalpingStrategy(cfg).evaluate({
            bbl: 99.0, bbu: 101.0, bbm: 100.0,
            rsi: 25.0, stoch: 10.0, "close": 98.0, atr: 1.0,
        })
        self.assertEqual(buy["signal"], "BUY")
        self.assertEqual(buy.get("take_profit_price"), 100.0)

        sell = BrsScalpingStrategy(cfg).evaluate({
            bbl: 99.0, bbu: 101.0, bbm: 100.0,
            rsi: 75.0, stoch: 85.0, "close": 102.0, atr: 1.0,
        })
        self.assertEqual(sell["signal"], "SELL")
        self.assertEqual(sell.get("take_profit_price"), 100.0)

        flat = BrsScalpingStrategy(cfg).evaluate({
            bbl: 99.0, bbu: 101.0, bbm: 100.0,
            rsi: 50.0, stoch: 50.0, "close": 100.0, atr: 1.0,
        })
        self.assertEqual(flat["signal"], "NONE")


class TestScreenerAndBacktest(unittest.TestCase):
    def setUp(self):
        self.candles = make_candles()
        self.screener = MarketScreenerService()

    def test_brs_screener_columns_and_crypto_backtest(self):
        from app.services.bots.indicators import (
            atr_col,
            bb_lower_col,
            bb_mid_col,
            bb_upper_col,
            stoch_k_col,
        )
        import math

        candles = make_candles(count=180, drift=0.0)
        for i, c in enumerate(candles):
            phase = math.sin(i / 8.0)
            px = 100 + 5 * phase
            c["open"] = px - 0.1
            c["close"] = px
            c["high"] = px + 0.4
            c["low"] = px - 0.4

        cfg = merge_strategy_config("BRS_SCALPING", {})
        df = self.screener.process_candles(
            "ETHUSDT", candles, cfg, "BRS_SCALPING", full_history=True
        )
        self.assertFalse(df.empty)
        for col in (
            bb_lower_col(cfg["bb_length"], cfg["bb_std"]),
            bb_upper_col(cfg["bb_length"], cfg["bb_std"]),
            bb_mid_col(cfg["bb_length"], cfg["bb_std"]),
            rsi_col(cfg["rsi_length"]),
            stoch_k_col(cfg["stoch_k"], cfg["stoch_d"], cfg["stoch_smooth"]),
            atr_col(cfg["atr_length"]),
        ):
            self.assertIn(col, df.columns, col)

        result = BacktesterService(self.screener).run_backtest(
            "ETHUSDT",
            "BRS_SCALPING",
            {**cfg, "allocation": 1000, "direction_mode": "BOTH", "sim_mode": "research"},
            candles,
        )
        self.assertNotIn("error", result)
        self.assertGreater(int(result.get("trade_count") or 0), 0)

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

    def test_donchian_screener_has_atr_median(self):
        config = {"atr_length": 14, "breakout_length": 20, "exit_length": 10}
        df = self.screener.process_candles("TEST", self.candles, config, "DONCHIAN_BREAKOUT")
        self.assertIn("ATR_14_median_20", df.columns)

    def test_donchian_skips_dual_breakout_bar(self):
        from app.services.bots.strategies_breakout import DonchianBreakoutStrategy

        cfg = merge_strategy_config("DONCHIAN_BREAKOUT", {"breakout_length": 20, "exit_length": 10})
        row = {
            "close": 100,
            "high": 110,
            "low": 90,
            "ATR_14": 2.0,
            "ATR_14_median_20": 1.5,
            "dc_high_20": 105,
            "dc_low_20": 95,
            "dc_high_10": 108,
            "dc_low_10": 92,
        }
        result = DonchianBreakoutStrategy(cfg).evaluate(row)
        self.assertEqual(result["signal"], "NONE")

    def test_backtest_short_only_blocks_long(self):
        backtester = BacktesterService(self.screener)
        result = backtester.run_backtest(
            "TEST",
            "MACD_RSI",
            {"direction_mode": "SHORT_ONLY"},
            self.candles,
        )
        long_entries = [t for t in result.get("trades", []) if not t.get("is_exit") and t.get("side") == "BUY"]
        self.assertEqual(len(long_entries), 0)


if __name__ == "__main__":
    unittest.main()
