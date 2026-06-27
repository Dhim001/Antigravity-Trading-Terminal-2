"""Tests for per-symbol chart drawing persistence."""

import unittest

from app.database import init_db
from app.api.handlers.chart_drawings import _load_drawings, _save_drawings


class TestChartDrawings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_round_trip(self):
        drawings = [
            {"id": "a", "tool": "trendline", "p1": {"time": 1, "price": 10},
             "p2": {"time": 5, "price": 20}, "color": "#3b82f6"},
            {"id": "b", "tool": "hline", "price": 42, "color": "#22c55e"},
        ]
        _save_drawings("TESTSYM", drawings)
        loaded = _load_drawings("TESTSYM")
        self.assertEqual(loaded, drawings)

    def test_overwrite_replaces(self):
        _save_drawings("TESTSYM2", [{"id": "x", "tool": "hline", "price": 1}])
        _save_drawings("TESTSYM2", [{"id": "y", "tool": "hline", "price": 2}])
        loaded = _load_drawings("TESTSYM2")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["id"], "y")

    def test_empty_for_unknown_symbol(self):
        self.assertEqual(_load_drawings("NOPE_SYMBOL_XYZ"), [])


if __name__ == "__main__":
    unittest.main()
