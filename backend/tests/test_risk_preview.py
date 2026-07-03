"""Tests for risk config and entry preview API helpers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.risk_preview import get_risk_config, preview_entry
from app.services.bots.correlation import summarize_basket_correlation


class TestRiskPreview(unittest.TestCase):
    def test_get_risk_config_shape(self):
        cfg = get_risk_config()
        self.assertIn("kill_switch", cfg)
        self.assertIn("time_controls", cfg)
        self.assertIn("portfolio_limits", cfg)
        self.assertTrue(cfg.get("env_readonly"))

    def test_preview_entry_requires_price_or_notional(self):
        oms = MagicMock()
        oms.get_account_data.return_value = {"positions": {}, "balances": {}}
        oms.feed = None
        result = preview_entry(oms, symbol="AAPL", side="BUY")
        self.assertIn("error", result)

    @patch("app.services.risk_preview.is_no_trade_window", return_value=(False, ""))
    @patch("app.services.risk_preview.compute_drawdown")
    @patch("app.services.risk_preview._mark_price", return_value=100.0)
    def test_preview_entry_allowed(self, _price, mock_dd, _window):
        from app.services.bots.risk_monitor import DrawdownSnapshot

        mock_dd.return_value = DrawdownSnapshot(
            account_equity=10000,
            cash_equity=10000,
            equity_peak=10000,
            current_drawdown_pct=1.0,
            max_drawdown_pct=15.0,
            kill_switch_enabled=True,
            kill_switch_tripped=False,
            kill_switch_tripped_at=None,
        )
        gate = MagicMock()
        gate.validate_portfolio.return_value = MagicMock(allowed=True, reason="OK", quantity=10.0)

        oms = MagicMock()
        result = preview_entry(
            oms,
            symbol="AAPL",
            side="BUY",
            notional=1000,
            risk_gate=gate,
        )
        self.assertTrue(result.get("allowed"))
        self.assertEqual(result.get("symbol"), "AAPL")
        self.assertEqual(len(result.get("checks") or []), 4)


class TestBasketCorrelation(unittest.TestCase):
    def test_summarize_requires_two_symbols(self):
        out = summarize_basket_correlation(["AAPL"])
        self.assertFalse(out["warning"])
        self.assertIn("2 symbols", out["message"])

    @patch("app.services.bots.correlation.get_price_correlation_matrix")
    def test_summarize_high_pair(self, mock_matrix):
        mock_matrix.return_value = {
            "symbols": ["AAPL", "MSFT"],
            "matrix": [[1.0, 0.85], [0.85, 1.0]],
            "period": "60d",
            "source": "test",
        }
        with patch(
            "app.services.bots.correlation.resolve_correlation_group",
            side_effect=lambda s: s,
        ):
            out = summarize_basket_correlation(["AAPL", "MSFT"], threshold=0.7)
        self.assertTrue(out["warning"])
        self.assertEqual(len(out["high_pairs"]), 1)


if __name__ == "__main__":
    unittest.main()
