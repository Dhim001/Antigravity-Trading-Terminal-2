"""Quote-aware cash helpers for the dual USD/USDT paper ledger."""

import unittest

from unittest import mock

from app.services.account_cash import (
    cash_available,
    cash_for_symbol,
    quote_asset_for_symbol,
    resolve_quote_asset,
    total_quote_available,
    total_quote_balance,
)
from app.services.bots.portfolio_risk import build_portfolio_snapshot


class _FakeOms:
    def __init__(self, account: dict):
        self._account = account

    def get_account_data(self):
        return self._account


class AccountCashTests(unittest.TestCase):
    def test_quote_asset_for_symbol(self):
        self.assertEqual(quote_asset_for_symbol("BTCUSDT"), "USDT")
        self.assertEqual(quote_asset_for_symbol("ethusdt"), "USDT")
        self.assertEqual(quote_asset_for_symbol("AAPL"), "USD")
        self.assertEqual(quote_asset_for_symbol(None), "USD")

    def test_cash_for_symbol_picks_correct_bucket(self):
        bals = {
            "USD": {"balance": 10_000, "locked": 500},
            "USDT": {"balance": 100_000, "locked": 0},
            "BTC": {"balance": 1.5, "locked": 0},
        }
        bal, locked, avail = cash_for_symbol(bals, "BTCUSDT")
        self.assertEqual(bal, 100_000)
        self.assertEqual(locked, 0)
        self.assertEqual(avail, 100_000)

        bal, locked, avail = cash_for_symbol(bals, "AAPL")
        self.assertEqual(bal, 10_000)
        self.assertEqual(locked, 500)
        self.assertEqual(avail, 9_500)

    def test_resolve_quote_falls_back_when_bucket_absent(self):
        # Alpaca live: USD only, crypto symbols still end in USDT.
        alpaca = {"USD": {"balance": 50_000, "locked": 0}}
        self.assertEqual(resolve_quote_asset(alpaca, "BTCUSDT"), "USD")
        self.assertEqual(cash_for_symbol(alpaca, "BTCUSDT")[2], 50_000)
        # Dual ledger: never fall back away from USDT when both exist.
        dual = {
            "USD": {"balance": 10_000, "locked": 0},
            "USDT": {"balance": 100_000, "locked": 0},
        }
        self.assertEqual(resolve_quote_asset(dual, "BTCUSDT"), "USDT")
        self.assertEqual(cash_for_symbol(dual, "BTCUSDT")[2], 100_000)

    def test_totals_ignore_base_assets(self):
        bals = {
            "USD": {"balance": 10_000, "locked": 0},
            "USDT": {"balance": 100_000, "locked": 1_000},
            "BTC": {"balance": 2.0, "locked": 0},
        }
        self.assertEqual(total_quote_balance(bals), 110_000)
        self.assertEqual(total_quote_available(bals), 109_000)
        self.assertEqual(cash_available(bals, "USDT"), 99_000)

    @mock.patch("app.services.bots.portfolio_risk.list_bot_exposures", return_value=[])
    def test_portfolio_snapshot_sums_quote_cash(self, _mock_bots):
        oms = _FakeOms({
            "balances": {
                "USD": {"balance": 100_000, "locked": 0},
                "USDT": {"balance": 100_000, "locked": 0},
                "BTC": {"balance": 1.0, "locked": 0},
            },
            "positions": {},
        })
        snap = build_portfolio_snapshot(oms)
        # Cash component is USD+USDT; BTC base balance is not added as cash.
        self.assertEqual(snap.cash_balance, 200_000)
        self.assertEqual(snap.account_equity, 200_000)


if __name__ == "__main__":
    unittest.main()
