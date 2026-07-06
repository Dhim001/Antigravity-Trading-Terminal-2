"""Tests for DB stats TTL cache."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import db_stats_cache


class DbStatsCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        db_stats_cache.invalidate_db_stats_cache()

    def test_returns_isolated_copy(self) -> None:
        base = {"positions_count": 1, "archive": {"pending_flush": 0}}
        with patch("app.database.get_db_stats", return_value=base):
            first = db_stats_cache.get_db_stats_cached(force_refresh=True)
            first["clients"] = 3
            first["archive"]["pending_flush"] = 9
            second = db_stats_cache.get_db_stats_cached()
        self.assertEqual(second["positions_count"], 1)
        self.assertNotIn("clients", second)
        self.assertEqual(second["archive"]["pending_flush"], 0)


if __name__ == "__main__":
    unittest.main()
