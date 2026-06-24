"""Tests for Massive HT limit resolution."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services.massive_ht_limits import (
    MASSIVE_HT_LIMIT_ANALYSIS,
    MASSIVE_HT_LIMIT_CHART,
    massive_ht_limit,
    massive_ht_store_cap,
)


class TestMassiveHtLimits(unittest.TestCase):
    def test_chart_default(self) -> None:
        self.assertEqual(massive_ht_limit("1h", purpose="chart"), MASSIVE_HT_LIMIT_CHART)

    def test_analysis_default(self) -> None:
        self.assertEqual(massive_ht_limit("1h", purpose="analysis"), MASSIVE_HT_LIMIT_ANALYSIS)
        self.assertGreater(massive_ht_limit("1h", purpose="analysis"), MASSIVE_HT_LIMIT_CHART)

    def test_explicit_overrides_purpose(self) -> None:
        self.assertEqual(massive_ht_limit("1h", purpose="analysis", explicit=500), 500)

    @patch.dict(os.environ, {"MASSIVE_HT_LIMIT_1H_ANALYSIS": "3000"}, clear=False)
    def test_per_tf_env_override(self) -> None:
        # Re-import to pick up env — limits module reads env at import time for defaults,
        # but _per_tf_limit reads os.environ at call time.
        self.assertEqual(massive_ht_limit("1h", purpose="analysis"), 3000)

    def test_store_cap_matches_analysis(self) -> None:
        self.assertEqual(massive_ht_store_cap("4h"), massive_ht_limit("4h", purpose="analysis"))


if __name__ == "__main__":
    unittest.main()
