"""SL/TP must not evaluate against unseeded Alpaca SYMBOLS defaults (e.g. BTC=63000)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class SlTpSeedGateTests(unittest.TestCase):
    def test_alpaca_feed_exposes_empty_seeded_set(self) -> None:
        from app.services.alpaca_feed import AlpacaFeedService

        feed = AlpacaFeedService()
        self.assertIsInstance(feed._seeded, set)
        self.assertEqual(len(feed._seeded), 0)

    def test_check_sl_tp_skips_unseeded_symbol(self) -> None:
        from app.services.sim_oms import SimulatedOMSService

        feed = MagicMock()
        feed._seeded = set()  # nothing seeded yet — startup defaults
        feed._symbols = {
            "BTCUSDT": {"price": 63000.0, "quote": {}},
        }
        oms = SimulatedOMSService.__new__(SimulatedOMSService)
        oms.feed = feed

        with patch("app.services.sim_oms.bot_positions") as bp, patch(
            "app.services.sim_oms.get_connection"
        ) as get_conn:
            bp.list_owners_grouped.return_value = {
                "BTCUSDT": [
                    {
                        "bot_id": "bot-ml",
                        "size": 0.03,
                        "avg_price": 63805.7,
                        "stop_loss_percent": 2.0,
                        "take_profit_percent": 3.0,
                        "stop_loss_price": 63077.4,
                        "take_profit_price": 65719.87,
                        "high_watermark": 64364.0,
                        "low_watermark": None,
                        "entry_atr": None,
                        "bot_config": {},
                        "timeframe": "1m",
                    }
                ]
            }
            conn = MagicMock()
            cursor = MagicMock()
            cursor.fetchall.return_value = []
            conn.cursor.return_value = cursor
            get_conn.return_value = conn

            fills, logs, exits = oms.check_sl_tp_triggers()

        self.assertEqual(fills, [])
        self.assertEqual(logs, [])
        self.assertEqual(exits, [])
        bp.evaluate_risk_trigger.assert_not_called()

    def test_check_sl_tp_runs_after_symbol_seeded(self) -> None:
        from app.services.sim_oms import SimulatedOMSService

        feed = MagicMock()
        feed._seeded = {"BTCUSDT"}
        feed._symbols = {
            "BTCUSDT": {"price": 64000.0, "quote": {}},
        }
        oms = SimulatedOMSService.__new__(SimulatedOMSService)
        oms.feed = feed

        with patch("app.services.sim_oms.bot_positions") as bp, patch(
            "app.services.sim_oms.get_connection"
        ) as get_conn:
            bp.list_owners_grouped.return_value = {
                "BTCUSDT": [
                    {
                        "bot_id": "bot-ml",
                        "size": 0.03,
                        "avg_price": 63805.7,
                        "stop_loss_percent": 2.0,
                        "take_profit_percent": 3.0,
                        "stop_loss_price": 62529.0,
                        "take_profit_price": 65719.87,
                        "high_watermark": 63805.7,
                        "low_watermark": None,
                        "entry_atr": None,
                        "bot_config": {},
                        "timeframe": "1m",
                    }
                ]
            }
            bp.evaluate_risk_trigger.return_value = (None, 62529.0, 64000.0, None)
            conn = MagicMock()
            cursor = MagicMock()
            cursor.fetchall.return_value = []
            conn.cursor.return_value = cursor
            get_conn.return_value = conn

            fills, logs, exits = oms.check_sl_tp_triggers()

        self.assertEqual(fills, [])
        self.assertEqual(exits, [])
        bp.evaluate_risk_trigger.assert_called_once()


if __name__ == "__main__":
    unittest.main()
