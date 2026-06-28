"""Exposure heatmap analytics tests."""

import unittest
from unittest import mock

from app.services.analytics.exposure import (
    asset_class_for_symbol,
    collect_position_exposures,
    get_exposure_heatmap,
    sector_for_symbol,
)


class _FakeOms:
    def __init__(self, positions=None, balances=None):
        self._positions = positions or {}
        self._balances = balances or {"USD": {"balance": 50_000, "locked": 0}}

    def get_account_data(self):
        return {
            "balances": self._balances,
            "positions": self._positions,
            "orders": [],
        }


class ExposureHeatmapTests(unittest.TestCase):
    @mock.patch("app.services.bots.correlation.RISK_DYNAMIC_CORRELATION_ENABLED", False)
    def test_asset_class_and_sector_labels(self):
        self.assertEqual(asset_class_for_symbol("BTCUSDT"), "Crypto")
        self.assertEqual(asset_class_for_symbol("SPY"), "Index ETF")
        self.assertEqual(asset_class_for_symbol("AAPL"), "US Equity")
        self.assertEqual(sector_for_symbol("AAPL"), "TECH")
        self.assertEqual(sector_for_symbol("BTCUSDT"), "CRYPTO_MAJOR")

    @mock.patch("app.services.analytics.exposure.list_bot_exposures")
    @mock.patch("app.services.analytics.exposure.build_portfolio_snapshot")
    def test_get_exposure_heatmap_aggregates(self, mock_snapshot, mock_list):
        from app.services.bots.portfolio_risk import PortfolioSnapshot

        mock_snapshot.return_value = PortfolioSnapshot(
            account_equity=100_000,
            gross_exposure=25_000,
            group_exposure={"TECH": 15_000, "CRYPTO_MAJOR": 10_000},
            symbol_exposure={"AAPL": 15_000, "BTCUSDT": 10_000},
        )
        mock_list.return_value = [
            {"bot_id": "b1", "symbol": "AAPL", "size": 50, "avg_price": 300},
            {"bot_id": "b2", "symbol": "BTCUSDT", "size": 0.1, "avg_price": 100_000},
        ]

        conn_patch = mock.patch("app.services.analytics.exposure.get_connection")
        with conn_patch as mock_conn:
            cursor = mock_conn.return_value.cursor.return_value
            cursor.fetchall.return_value = [
                {"bot_id": "b1", "symbol": "AAPL", "size": 50, "avg_price": 300, "strategy": "MACD_RSI"},
                {"bot_id": "b2", "symbol": "BTCUSDT", "size": 0.1, "avg_price": 100_000, "strategy": "TICK_MOMENTUM"},
            ]
            result = get_exposure_heatmap(_FakeOms())

        self.assertGreater(result["total_notional"], 0)
        self.assertEqual(len(result["by_asset_class"]), 2)
        self.assertEqual(len(result["by_strategy"]), 2)
        self.assertTrue(result["cross_strategy_sector"]["matrix"])

    @mock.patch("app.services.analytics.exposure.build_portfolio_snapshot")
    @mock.patch("app.services.analytics.exposure.list_bot_exposures", return_value=[])
    def test_empty_exposure(self, _list, mock_snapshot):
        from app.services.bots.portfolio_risk import PortfolioSnapshot

        mock_snapshot.return_value = PortfolioSnapshot(
            account_equity=10_000,
            gross_exposure=0,
            group_exposure={},
            symbol_exposure={},
        )
        result = get_exposure_heatmap(_FakeOms())
        self.assertEqual(result["position_count"], 0)
        self.assertEqual(result["by_sector"], [])


if __name__ == "__main__":
    unittest.main()
