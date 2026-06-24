"""Optional Massive API integration test (skipped without API key)."""

from __future__ import annotations

import asyncio
import os
import unittest


def _has_api_key() -> bool:
    return bool(os.environ.get("MASSIVE_API_KEY", "").strip())


@unittest.skipUnless(_has_api_key(), "MASSIVE_API_KEY not set")
class TestMassiveIntegration(unittest.TestCase):
    def test_rest_and_smoke_exit_code(self) -> None:
        """Run smoke script logic; accept ok or market-closed timeout."""
        import subprocess
        import sys
        from pathlib import Path

        script = Path(__file__).resolve().parents[1] / "scripts" / "massive_smoke_test.py"
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        # 0=ok, 5=stocks timeout when market closed
        self.assertIn(
            proc.returncode,
            (0, 5),
            msg=f"stdout={proc.stdout}\nstderr={proc.stderr}",
        )
        self.assertIn("REST AAPL", proc.stdout)

    def test_feed_status_shape(self) -> None:
        from app.services.massive_feed import MassiveFeedService

        feed = MassiveFeedService()
        status = feed.massive_status
        for key in (
            "connected",
            "stocks_mode",
            "crypto_mode",
            "quotes_enabled",
            "poll_fallback",
        ):
            self.assertIn(key, status)


if __name__ == "__main__":
    unittest.main()
