import unittest

from app.services.bots.portfolio_risk import (
    PortfolioSnapshot,
    symbol_correlation_group,
    validate_portfolio_entry,
)
from app.services.bots.tick_strategies import get_tick_strategy, merge_tick_config
from app.services.bots.tick_screener import TickScreener


class TestPortfolioRisk(unittest.TestCase):
    def test_symbol_groups(self):
        self.assertEqual(symbol_correlation_group("AAPL"), "TECH")
        self.assertEqual(symbol_correlation_group("BTCUSDT"), "CRYPTO_MAJOR")
        self.assertEqual(symbol_correlation_group("SPY"), "INDEX_ETF")

    def test_gross_cap_blocks_entry(self):
        snap = PortfolioSnapshot(
            account_equity=100_000,
            gross_exposure=80_000,
            group_exposure={"TECH": 40_000},
            symbol_exposure={"AAPL": 40_000},
        )
        allowed, reason, _ = validate_portfolio_entry(snap, "MSFT", "BUY", 1, 200)
        self.assertFalse(allowed)
        self.assertIn("gross exposure", reason.lower())

    def test_group_cap_blocks_entry(self):
        snap = PortfolioSnapshot(
            account_equity=100_000,
            gross_exposure=30_000,
            group_exposure={"TECH": 40_000},
            symbol_exposure={"AAPL": 40_000},
        )
        allowed, reason, _ = validate_portfolio_entry(snap, "NVDA", "BUY", 1, 500)
        self.assertFalse(allowed)
        self.assertIn("TECH", reason)


class TestTickStrategies(unittest.TestCase):
    def test_momentum_buy_signal(self):
        screener = TickScreener()
        base = 100.0
        for i in range(25):
            screener.record("AAPL", base + i * 0.05, i * 1000)
        ctx = screener.context("AAPL", base + 1.5, 25000, 20)
        strat = get_tick_strategy("TICK_MOMENTUM", merge_tick_config("TICK_MOMENTUM", {}))
        result = strat.evaluate(ctx, base + 1.5)
        self.assertEqual(result.get("signal"), "BUY")


if __name__ == "__main__":
    unittest.main()
