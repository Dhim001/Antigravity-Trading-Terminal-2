"""Unit tests for Massive symbol mapping (no API required)."""

from __future__ import annotations

import unittest

from app.services.massive_symbols import (
    build_pair_to_terminal,
    is_crypto_terminal_symbol,
    terminal_to_massive_rest_ticker,
    terminal_to_massive_ws_pair,
)


class TestMassiveSymbols(unittest.TestCase):
    def test_crypto_detection(self) -> None:
        self.assertTrue(is_crypto_terminal_symbol("BTCUSDT"))
        self.assertFalse(is_crypto_terminal_symbol("AAPL"))

    def test_rest_ticker_equity(self) -> None:
        self.assertEqual(terminal_to_massive_rest_ticker("AAPL"), "AAPL")

    def test_rest_ticker_crypto(self) -> None:
        self.assertEqual(
            terminal_to_massive_rest_ticker("BTCUSDT", {"asset": "BTC"}),
            "X:BTCUSD",
        )

    def test_ws_pair_crypto(self) -> None:
        self.assertEqual(
            terminal_to_massive_ws_pair("ETHUSDT", {"asset": "ETH"}),
            "ETH-USD",
        )

    def test_pair_to_terminal_map(self) -> None:
        mapping = build_pair_to_terminal({"BTCUSDT": {"asset": "BTC", "quote": "USDT"}})
        self.assertEqual(mapping["BTC-USD"], "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
