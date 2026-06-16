"""Rate limit helper tests."""

import unittest

from app.api.rate_limit import rate_limit_allow, reset_local_rate_limits


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        reset_local_rate_limits()

    def test_blocks_rapid_calls(self):
        self.assertTrue(rate_limit_allow("test:a", 1.0))
        self.assertFalse(rate_limit_allow("test:a", 1.0))

    def test_different_keys_independent(self):
        self.assertTrue(rate_limit_allow("test:a", 1.0))
        self.assertTrue(rate_limit_allow("test:b", 1.0))


if __name__ == "__main__":
    unittest.main()
