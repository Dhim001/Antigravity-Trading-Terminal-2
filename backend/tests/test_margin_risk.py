"""Margin / leverage risk tests."""

import unittest
from unittest import mock

from app.services.bots.margin_risk import (
    MarginSnapshot,
    build_margin_snapshot,
    entry_margin_required,
    validate_margin_entry,
)
from app.services.bots.portfolio_risk import PortfolioSnapshot, validate_portfolio_entry


class _FakeOms:
    def __init__(self, account: dict):
        self._account = account

    def get_account_data(self):
        return self._account


class MarginRiskTests(unittest.TestCase):
    def test_entry_margin_required_scales_with_leverage(self):
        self.assertEqual(entry_margin_required(10_000, 1), 10_000)
        self.assertEqual(entry_margin_required(10_000, 5), 2_000)

    @mock.patch("app.services.bots.margin_risk.RISK_MAX_LEVERAGE", 1.0)
    def test_validate_margin_blocks_excessive_leverage(self):
        margin = MarginSnapshot(
            enabled=True,
            source="sim",
            account_equity=100_000,
            available_cash=50_000,
            margin_used=10_000,
            margin_capacity=100_000,
            utilization_pct=10.0,
            max_leverage=1.0,
        )
        ok, reason, _ = validate_margin_entry(
            margin, price=100, quantity=10, leverage=5,
        )
        self.assertFalse(ok)
        self.assertIn("leverage", reason.lower())

    @mock.patch("app.services.bots.margin_risk.RISK_MAX_MARGIN_UTILIZATION_PCT", 85.0)
    def test_validate_margin_blocks_utilization_breach(self):
        margin = MarginSnapshot(
            enabled=True,
            source="sim",
            account_equity=100_000,
            available_cash=20_000,
            margin_used=84_000,
            margin_capacity=100_000,
            utilization_pct=84.0,
            max_leverage=1.0,
        )
        ok, reason, capped = validate_margin_entry(
            margin, price=100, quantity=20, leverage=1,
        )
        self.assertTrue(ok)
        self.assertIsNotNone(capped)
        self.assertLess(capped, 20)
        self.assertIn("margin", reason.lower())

    @mock.patch("app.services.bots.correlation.RISK_DYNAMIC_CORRELATION_ENABLED", False)
    @mock.patch("app.services.bots.margin_risk.RISK_MAX_MARGIN_UTILIZATION_PCT", 85.0)
    def test_portfolio_entry_applies_margin_after_gross_cap(self):
        snap = PortfolioSnapshot(
            account_equity=100_000,
            gross_exposure=10_000,
            group_exposure={"TECH": 5_000},
            symbol_exposure={"AAPL": 5_000},
        )
        margin = MarginSnapshot(
            enabled=True,
            source="sim",
            account_equity=100_000,
            available_cash=5_000,
            margin_used=80_000,
            margin_capacity=100_000,
            utilization_pct=80.0,
            max_leverage=1.0,
        )
        ok, reason, capped = validate_portfolio_entry(
            snap, "MSFT", "BUY", 100, 100,
            margin=margin,
            entry_leverage=1,
        )
        self.assertTrue(ok)
        self.assertIsNotNone(capped)
        self.assertEqual(capped, 50.0)
        self.assertIn("margin", reason.lower())

    def test_build_margin_snapshot_from_account_margin_block(self):
        oms = _FakeOms({
            "balances": {"USD": {"balance": 25_000, "locked": 0}},
            "positions": {},
            "margin": {
                "source": "alpaca",
                "equity": 100_000,
                "buying_power": 40_000,
                "available_cash": 40_000,
                "margin_used": 60_000,
                "max_leverage": 1,
            },
        })
        portfolio = PortfolioSnapshot(
            account_equity=100_000,
            gross_exposure=55_000,
            group_exposure={},
            symbol_exposure={},
        )
        margin = build_margin_snapshot(oms, portfolio)
        self.assertEqual(margin.source, "alpaca")
        self.assertEqual(margin.available_cash, 40_000)
        self.assertEqual(margin.margin_used, 60_000)
        self.assertEqual(margin.utilization_pct, 60.0)


if __name__ == "__main__":
    unittest.main()
