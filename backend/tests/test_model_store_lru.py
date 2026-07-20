"""Unit tests for ModelCacheLru / bind_dict_cache (MEMORY_CENTRIC_REVIEW #2)."""

from __future__ import annotations

import time
import unittest

from app.services.bots.model_store_lru import bind_dict_cache


class ModelCacheLruTests(unittest.TestCase):
    def test_max_entries_evicts_oldest(self):
        store: dict[str, int] = {}
        lru = bind_dict_cache(store, max_entries=2, ttl_sec=None)
        store["a"] = 1
        lru.touch("a")
        store["b"] = 2
        lru.touch("b")
        store["c"] = 3
        lru.touch("c")
        self.assertNotIn("a", store)
        self.assertEqual(set(store), {"b", "c"})
        self.assertEqual(len(lru), 2)

    def test_ttl_expires_idle(self):
        store: dict[str, int] = {}
        lru = bind_dict_cache(store, max_entries=10, ttl_sec=60.0)
        store["old"] = 1
        lru.touch("old")
        # Force age past TTL
        lru._order["old"] = time.time() - 120
        store["new"] = 2
        lru.touch("new")
        self.assertNotIn("old", store)
        self.assertIn("new", store)

    def test_discard_prefix(self):
        sessions: dict[str, str] = {}
        meta: dict[str, str] = {}
        lru = bind_dict_cache(sessions, meta, max_entries=10, ttl_sec=None)
        for key in ("BTCUSDT|v1", "BTCUSDT|v2", "ETHUSDT|v1"):
            sessions[key] = key
            meta[key] = key
            lru.touch(key)
        removed = lru.discard_prefix("BTCUSDT")
        self.assertEqual(sorted(removed), ["BTCUSDT|v1", "BTCUSDT|v2"])
        self.assertEqual(list(sessions.keys()), ["ETHUSDT|v1"])
        self.assertEqual(list(meta.keys()), ["ETHUSDT|v1"])
        # Bare prefix must not clobber longer symbols (BTC vs BTCUSDT).
        sessions["BTC|v1"] = "BTC|v1"
        meta["BTC|v1"] = "BTC|v1"
        lru.touch("BTC|v1")
        sessions["BTCUSDT|v3"] = "BTCUSDT|v3"
        meta["BTCUSDT|v3"] = "BTCUSDT|v3"
        lru.touch("BTCUSDT|v3")
        removed_btc = lru.discard_prefix("BTC")
        self.assertEqual(removed_btc, ["BTC|v1"])
        self.assertIn("BTCUSDT|v3", sessions)
        self.assertIn("ETHUSDT|v1", sessions)

    def test_clear_evicts_payloads(self):
        store: dict[str, int] = {}
        lru = bind_dict_cache(store, max_entries=5, ttl_sec=None)
        store["x"] = 1
        lru.touch("x")
        lru.clear()
        self.assertEqual(store, {})
        self.assertEqual(len(lru), 0)


if __name__ == "__main__":
    unittest.main()
