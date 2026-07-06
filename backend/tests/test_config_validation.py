"""Tests for bot config validation."""

import unittest

from app.services.bots.config_validation import (
    normalize_bot_config,
    normalize_confirm_timeframe,
    sanitize_bot_config,
)


class ConfigValidationTests(unittest.TestCase):
    def test_empty_confirm_timeframe_allowed(self):
        self.assertEqual(normalize_confirm_timeframe(""), "")
        self.assertEqual(normalize_confirm_timeframe(None), "")

    def test_valid_confirm_timeframe_normalized(self):
        self.assertEqual(normalize_confirm_timeframe("15m"), "15m")
        self.assertEqual(normalize_confirm_timeframe("1H"), "1h")

    def test_bare_minutes_coerced(self):
        self.assertEqual(normalize_confirm_timeframe("15"), "15m")

    def test_invalid_confirm_timeframe_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            normalize_confirm_timeframe("15x")
        self.assertIn("Invalid confirm_timeframe", str(ctx.exception))

    def test_sanitize_auto_corrects_bare_minutes(self):
        cfg, warnings = sanitize_bot_config({"confirm_timeframe": "15"})
        self.assertEqual(cfg["confirm_timeframe"], "15m")
        self.assertTrue(any("Auto-corrected" in w for w in warnings))

    def test_sanitize_clears_unrecoverable_value(self):
        cfg, warnings = sanitize_bot_config({"confirm_timeframe": "bad"})
        self.assertEqual(cfg["confirm_timeframe"], "")
        self.assertTrue(warnings)

    def test_normalize_bot_config_rejects_same_as_bot_timeframe(self):
        with self.assertRaises(ValueError):
            normalize_bot_config(
                {"confirm_timeframe": "1m"},
                bot_timeframe="1m",
            )

    def test_update_merge_coerces_bare_minutes(self):
        cfg = normalize_bot_config({"confirm_timeframe": "15", "min_score": 2})
        self.assertEqual(cfg["confirm_timeframe"], "15m")
        self.assertEqual(cfg["min_score"], 2)


if __name__ == "__main__":
    unittest.main()
