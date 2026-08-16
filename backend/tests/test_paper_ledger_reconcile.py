"""Orphan base-inventory reconciliation for the paper ledger."""

import os
import tempfile
import unittest

import app.db.connection as db_conn
from app.database import get_connection, init_db
from app.services.paper_ledger import (
    clip_qty_to_spendable,
    quote_cash_covers,
    reconcile_base_inventories,
    repair_empty_quote_cash,
    reconstruct_quote_cash_from_fills,
)


class _FakeCursor:
    def __init__(self, accounts: dict, positions: dict):
        self.accounts = {k: {"asset": k, "balance": v, "locked": 0.0} for k, v in accounts.items()}
        self.positions = dict(positions)
        self._result = []
        self.updates: list[tuple] = []

    def execute(self, sql: str, params=()):
        sql_n = " ".join(sql.split())
        if sql_n.startswith("SELECT asset, balance FROM accounts"):
            self._result = [
                {"asset": a, "balance": row["balance"]}
                for a, row in self.accounts.items()
            ]
        elif sql_n.startswith("SELECT size FROM positions WHERE symbol"):
            sym = params[0]
            self._result = [{"size": float(self.positions.get(sym, 0.0))}]
        elif sql_n.startswith("UPDATE accounts SET balance"):
            bal, asset = params
            self.updates.append((asset, bal))
            if asset in self.accounts:
                self.accounts[asset]["balance"] = bal
            else:
                self.accounts[asset] = {"asset": asset, "balance": bal, "locked": 0.0}
            self._result = []
        elif sql_n.startswith("INSERT INTO accounts"):
            asset, bal, _locked = params
            self.updates.append((asset, bal))
            self.accounts[asset] = {"asset": asset, "balance": bal, "locked": 0.0}
            self._result = []
        else:
            self._result = []

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None


class ReconcileBaseInventoryTests(unittest.TestCase):
    def test_zeros_orphan_base_when_position_flat(self):
        cur = _FakeCursor(
            accounts={"USDT": 100_000, "ADA": 30_255.67},
            positions={"ADAUSDT": 0.0},
        )
        meta = {"ADAUSDT": {"asset": "ADA", "quote": "USDT"}}
        fixed = reconcile_base_inventories(cur, meta)
        self.assertEqual(fixed, ["ADA"])
        self.assertAlmostEqual(cur.accounts["ADA"]["balance"], 0.0)
        self.assertAlmostEqual(cur.accounts["USDT"]["balance"], 100_000)

    def test_syncs_base_to_long_size(self):
        cur = _FakeCursor(
            accounts={"USDT": 50_000, "BTC": 0.1},
            positions={"BTCUSDT": 0.5},
        )
        meta = {"BTCUSDT": {"asset": "BTC", "quote": "USDT"}}
        fixed = reconcile_base_inventories(cur, meta)
        self.assertEqual(fixed, ["BTC"])
        self.assertAlmostEqual(cur.accounts["BTC"]["balance"], 0.5)

    def test_noop_when_already_aligned(self):
        cur = _FakeCursor(
            accounts={"USDT": 50_000, "BTC": 0.5},
            positions={"BTCUSDT": 0.5},
        )
        meta = {"BTCUSDT": {"asset": "BTC", "quote": "USDT"}}
        fixed = reconcile_base_inventories(cur, meta)
        self.assertEqual(fixed, [])


class QuoteCashHelpersTests(unittest.TestCase):
    def test_covers_allows_one_cent_dust(self):
        self.assertTrue(quote_cash_covers(921.37, 921.371))
        self.assertFalse(quote_cash_covers(921.37, 922.0))
        self.assertFalse(quote_cash_covers(0.0, 10.0))
        self.assertTrue(quote_cash_covers(10.0, 0.0))

    def test_clip_qty_trims_float_overshoot(self):
        qty = clip_qty_to_spendable(1.0, 100.0, 99.995)
        self.assertLessEqual(qty * 100.0, 99.995 + 1e-9)
        self.assertGreater(qty, 0.999)


class RepairEmptyQuoteCashTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._old_path = db_conn.DB_PATH
        db_conn.DB_PATH = os.path.join(self._tmpdir, "repair_quote.db")
        db_conn._pool = None
        init_db()
        self.meta = {"ETHUSDT": {"asset": "ETH", "quote": "USDT"}}

    def tearDown(self):
        db_conn._pool = None
        db_conn.DB_PATH = self._old_path

    def _fill(self, cur, oid, side, price, qty=1.0, symbol="ETHUSDT"):
        cur.execute(
            """
            INSERT INTO orders (
                id, symbol, type, side, price, quantity, status,
                filled_quantity, average_fill_price
            ) VALUES (?, ?, 'MARKET', ?, ?, ?, 'FILLED', ?, ?)
            """,
            (oid, symbol, side, price, qty, qty, price),
        )

    def test_restores_usdt_including_closed_trade_pnl(self):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE accounts SET balance = -0.07, locked = 0 WHERE asset = 'USDT'")
        self._fill(cur, "o1", "BUY", 100.0)
        self._fill(cur, "o2", "SELL", 110.0)
        conn.commit()
        repaired = repair_empty_quote_cash(cur, self.meta)
        conn.commit()
        self.assertEqual(repaired, ["USDT"])
        cur.execute("SELECT balance, locked FROM accounts WHERE asset = 'USDT'")
        row = cur.fetchone()
        conn.close()
        self.assertAlmostEqual(float(row["balance"]), 100010.0)
        self.assertAlmostEqual(float(row["locked"]), 0.0)

    def test_restores_usdt_including_short_cover_pnl(self):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE accounts SET balance = 0, locked = 0 WHERE asset = 'USDT'")
        self._fill(cur, "s1", "SELL", 100.0)
        self._fill(cur, "s2", "BUY", 90.0)
        conn.commit()
        cash, pos, _counts = reconstruct_quote_cash_from_fills(cur, self.meta)
        self.assertAlmostEqual(cash["USDT"]["balance"], 100010.0)
        self.assertEqual(pos, {})
        repaired = repair_empty_quote_cash(cur, self.meta)
        conn.commit()
        self.assertEqual(repaired, ["USDT"])
        cur.execute("SELECT balance FROM accounts WHERE asset = 'USDT'")
        self.assertAlmostEqual(float(cur.fetchone()["balance"]), 100010.0)
        conn.close()

    def test_skips_healthy_usdt_with_no_fills(self):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE accounts SET balance = 50000 WHERE asset = 'USDT'")
        conn.commit()
        self.assertEqual(repair_empty_quote_cash(cur, self.meta), [])
        cur.execute("SELECT balance FROM accounts WHERE asset = 'USDT'")
        self.assertAlmostEqual(float(cur.fetchone()["balance"]), 50000)
        conn.close()

    def test_upgrades_seed_reset_to_include_fill_pnl(self):
        """A $100k restore that dropped closed-trade P&L should be lifted."""
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE accounts SET balance = 100000, locked = 0 WHERE asset = 'USDT'")
        self._fill(cur, "o1", "BUY", 100.0)
        self._fill(cur, "o2", "SELL", 110.0)
        conn.commit()
        repaired = repair_empty_quote_cash(cur, self.meta)
        conn.commit()
        self.assertEqual(repaired, ["USDT"])
        cur.execute("SELECT balance FROM accounts WHERE asset = 'USDT'")
        self.assertAlmostEqual(float(cur.fetchone()["balance"]), 100010.0)
        conn.close()

    def test_refunds_vanished_inventory_at_cost(self):
        """Live book is flat but fills still hold a long — refund at cost, no extra PnL."""
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE accounts SET balance = 0, locked = 0 WHERE asset = 'USDT'")
        self._fill(cur, "o1", "BUY", 1800.0)
        conn.commit()
        repaired = repair_empty_quote_cash(cur, self.meta)
        conn.commit()
        self.assertEqual(repaired, ["USDT"])
        cur.execute("SELECT balance FROM accounts WHERE asset = 'USDT'")
        self.assertAlmostEqual(float(cur.fetchone()["balance"]), 100000.0)
        conn.close()

    def test_lifts_leaked_usd_to_include_equity_pnl(self):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE accounts SET balance = 97090 WHERE asset = 'USD'")
        self._fill(cur, "a1", "SELL", 100.0, symbol="AAPL")
        self._fill(cur, "a2", "BUY", 90.0, symbol="AAPL")
        conn.commit()
        meta = {**self.meta, "AAPL": {"asset": "AAPL", "quote": "USD"}}
        repaired = repair_empty_quote_cash(cur, meta)
        conn.commit()
        self.assertIn("USD", repaired)
        cur.execute("SELECT balance FROM accounts WHERE asset = 'USD'")
        self.assertAlmostEqual(float(cur.fetchone()["balance"]), 100010.0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
