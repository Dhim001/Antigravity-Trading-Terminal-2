"""Orphan base-inventory reconciliation for the paper ledger."""

import unittest

from app.services.paper_ledger import reconcile_base_inventories


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


if __name__ == "__main__":
    unittest.main()
