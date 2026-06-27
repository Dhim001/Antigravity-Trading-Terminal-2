"""Tests for account-as-budget risk sizing."""

import unittest

from app.services.bots.risk_sizing import (
    RISK_PCT,
    enrich_backtest_risk_config,
    entry_quantity_from_risk,
    entry_risk_amount,
    parse_risk_sizing_config,
)


class RiskSizingTests(unittest.TestCase):
    def test_parse_defaults_to_account_snapshot(self):
        cfg = parse_risk_sizing_config({})
        self.assertEqual(cfg["mode"], "account_snapshot")
        self.assertEqual(cfg["snapshot"], 10_000.0)

    def test_parse_risk_base_from_config(self):
        cfg = parse_risk_sizing_config({"risk_base": 50_000, "risk_base_mode": "account_snapshot"})
        self.assertEqual(cfg["snapshot"], 50_000.0)
        self.assertAlmostEqual(entry_risk_amount(cfg, 1000.0), 500.0)

    def test_simulated_equity_uses_running_equity(self):
        cfg = parse_risk_sizing_config({"risk_base_mode": "simulated_equity", "risk_base": 10_000})
        self.assertAlmostEqual(entry_risk_amount(cfg, 8_000.0), 80.0)

    def test_entry_quantity_respects_stop_distance(self):
        cfg = parse_risk_sizing_config({"risk_base": 100_000})
        qty = entry_quantity_from_risk(
            risk_cfg=cfg,
            simulated_equity=500,
            price=100.0,
            stop_loss=98.0,
        )
        self.assertAlmostEqual(qty, 500.0)

    def test_enrich_injects_account_balance(self):
        out = enrich_backtest_risk_config({"allocation": 1000}, 25_000.0)
        self.assertEqual(out["risk_base"], 25_000.0)
        self.assertEqual(out["risk_base_mode"], "account_snapshot")

    def test_enrich_preserves_client_risk_base(self):
        out = enrich_backtest_risk_config({"risk_base": 42_000}, 25_000.0)
        self.assertEqual(out["risk_base"], 42_000)

    def test_risk_pct_constant(self):
        self.assertEqual(RISK_PCT, 0.01)


if __name__ == "__main__":
    unittest.main()
