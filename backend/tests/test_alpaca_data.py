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


if __name__ == "__main__":
    unittest.main()
