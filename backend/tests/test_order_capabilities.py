"""Tests for broker order capability flags."""

import unittest

from app.services.order_capabilities import get_order_capabilities


class OrderCapabilitiesTests(unittest.TestCase):
    def test_simulated_partial_and_reverse(self):
        caps = get_order_capabilities()
        self.assertTrue(caps["partial_close"])
        self.assertIn("broker", caps)

    def test_keys_present(self):
        caps = get_order_capabilities()
        for key in (
            "partial_close",
            "reverse_position",
            "bracket_orders",
            "oco",
            "trailing_stop_manual",
            "order_preview_costs",
            "paper_shorts",
        ):
            self.assertIn(key, caps)


if __name__ == "__main__":
    unittest.main()
