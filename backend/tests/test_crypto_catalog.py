"""Crypto catalog / watchlist integration."""

from __future__ import annotations

import unittest

from app.config import (
    CORRELATION_GROUPS,
    CRYPTO_SYMBOLS,
    SCANNER_DEPLOY_WATCHLIST,
    _normalize_crypto_watch_symbol,
)
from app.services.bots.correlation import static_correlation_group
from app.services.synthetic_data import YF_SYMBOL_MAP


class TestCryptoCatalog(unittest.TestCase):
    def test_top_20_crypto_catalog(self):
        self.assertEqual(len(CRYPTO_SYMBOLS), 20)
        for sym, info in CRYPTO_SYMBOLS.items():
            self.assertTrue(sym.endswith("USDT"), sym)
            self.assertEqual(info["quote"], "USDT")
            self.assertTrue(info["asset"])
            self.assertGreater(info["decimals"], 0)
            self.assertGreater(info["price"], 0)

    def test_scanner_watchlist_defaults_to_catalog(self):
        self.assertEqual(set(SCANNER_DEPLOY_WATCHLIST), set(CRYPTO_SYMBOLS.keys()))
        self.assertTrue(all(s.endswith("USDT") for s in SCANNER_DEPLOY_WATCHLIST))

    def test_normalize_slash_and_usd_forms(self):
        self.assertEqual(_normalize_crypto_watch_symbol("BTC/USD"), "BTCUSDT")
        self.assertEqual(_normalize_crypto_watch_symbol("eth-usd"), "ETHUSDT")
        self.assertEqual(_normalize_crypto_watch_symbol("SOLUSD"), "SOLUSDT")
        self.assertEqual(_normalize_crypto_watch_symbol("avax"), "AVAXUSDT")
        self.assertEqual(_normalize_crypto_watch_symbol("NEARUSDT"), "NEARUSDT")

    def test_correlation_groups_cover_catalog(self):
        majors = set(CORRELATION_GROUPS["CRYPTO_MAJOR"])
        alts = set(CORRELATION_GROUPS["CRYPTO_ALT"])
        self.assertEqual(majors | alts, set(CRYPTO_SYMBOLS.keys()))
        self.assertEqual(static_correlation_group("BTCUSDT"), "CRYPTO_MAJOR")
        self.assertEqual(static_correlation_group("TRXUSDT"), "CRYPTO_ALT")
        self.assertEqual(static_correlation_group("SHIBUSDT"), "CRYPTO_ALT")

    def test_yfinance_map_covers_catalog(self):
        for sym in CRYPTO_SYMBOLS:
            self.assertIn(sym, YF_SYMBOL_MAP)
            self.assertTrue(YF_SYMBOL_MAP[sym].endswith("-USD"))


if __name__ == "__main__":
    unittest.main()
