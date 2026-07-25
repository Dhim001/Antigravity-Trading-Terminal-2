"""Tests for Alpaca SIP vs IEX feed auto-selection."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services import alpaca_data


class AlpacaDataFeedTests(unittest.TestCase):
    def setUp(self) -> None:
        alpaca_data._resolved_feed = None
        alpaca_data._resolved_ws_url = None

    def test_probe_sip_true_on_200(self) -> None:
        mock_resp = MagicMock(status_code=200)
        with patch.object(alpaca_data, "ALPACA_API_KEY", "k"), patch.object(
            alpaca_data, "ALPACA_SECRET_KEY", "s"
        ), patch("app.services.alpaca_data.httpx.Client") as client_cls:
            client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            self.assertTrue(alpaca_data.probe_sip_entitlement())

    def test_probe_sip_false_on_entitlement_error(self) -> None:
        mock_resp = MagicMock(status_code=422)
        mock_resp.json.return_value = {
            "code": 42210000,
            "message": "subscription does not permit querying recent SIP data",
        }
        with patch.object(alpaca_data, "ALPACA_API_KEY", "k"), patch.object(
            alpaca_data, "ALPACA_SECRET_KEY", "s"
        ), patch("app.services.alpaca_data.httpx.Client") as client_cls:
            client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            self.assertFalse(alpaca_data.probe_sip_entitlement())

    def test_resolve_auto_picks_iex_when_no_sip(self) -> None:
        with patch.dict("os.environ", {"ALPACA_DATA_FEED": "auto"}, clear=False), patch.object(
            alpaca_data, "probe_sip_entitlement", return_value=False
        ), patch.object(alpaca_data, "ALPACA_API_KEY", "k"), patch.object(
            alpaca_data, "ALPACA_SECRET_KEY", "s"
        ):
            self.assertEqual(alpaca_data.resolve_equity_data_feed(force_refresh=True), "iex")
            self.assertEqual(
                alpaca_data.get_alpaca_ws_url(force_refresh=True),
                "wss://stream.data.alpaca.markets/v2/iex",
            )

    def test_resolve_force_sip(self) -> None:
        with patch.dict("os.environ", {"ALPACA_DATA_FEED": "sip"}, clear=False):
            self.assertEqual(alpaca_data.resolve_equity_data_feed(force_refresh=True), "sip")

    def test_is_sip_entitlement_error(self) -> None:
        self.assertTrue(
            alpaca_data.is_sip_entitlement_error(
                422,
                {"code": 42210000, "message": "subscription does not permit querying recent SIP data"},
            )
        )

    def test_crypto_symbol_mapping_roundtrip(self) -> None:
        self.assertEqual(alpaca_data.terminal_to_alpaca_crypto("BTCUSDT"), "BTC/USD")
        self.assertEqual(alpaca_data.terminal_to_alpaca_crypto("ETH/USD"), "ETH/USD")
        self.assertEqual(alpaca_data.alpaca_crypto_to_terminal("BTC/USD"), "BTCUSDT")
        self.assertEqual(alpaca_data.alpaca_crypto_to_terminal("SOL/USDT"), "SOLUSDT")

    def test_option_symbol_detection(self) -> None:
        self.assertTrue(alpaca_data.is_option_symbol("AAPL250117C00200000"))
        self.assertTrue(alpaca_data.is_option_symbol("SPY260320P00500000"))
        self.assertFalse(alpaca_data.is_option_symbol("AAPL"))
        self.assertFalse(alpaca_data.is_option_symbol("BTCUSDT"))

    def test_crypto_and_options_urls(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            self.assertIn("crypto/us", alpaca_data.get_crypto_ws_url())
            self.assertIn("indicative", alpaca_data.get_options_ws_url())


if __name__ == "__main__":
    unittest.main()
