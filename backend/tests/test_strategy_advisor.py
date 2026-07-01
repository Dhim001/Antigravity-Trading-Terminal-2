"""Strategy advisor validation and heuristics."""

from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""

_TEST_DIR = tempfile.mkdtemp()
import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "advisor_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH

from app.services.bots.strategy_advisor import (  # noqa: E402
    build_advisor_context,
    validate_suggested_params,
    _heuristic_suggestion,
)


class StrategyAdvisorValidationTests(unittest.TestCase):
    def test_clamps_and_filters_params(self):
        patch, warnings = validate_suggested_params(
            "CHART_AGENT",
            {
                "min_confidence": 1.5,
                "min_score": 99,
                "unknown_key": 1,
                "block_elevated_vol": True,
            },
            base_config={"min_confidence": 0.55},
        )
        self.assertIn("min_confidence", patch)
        self.assertLessEqual(patch["min_confidence"], 0.95)
        self.assertIn("min_score", patch)
        self.assertLessEqual(patch["min_score"], 5)
        self.assertTrue(patch["block_elevated_vol"])
        self.assertTrue(any("unknown" in w for w in warnings))

    def test_heuristic_suggests_tighter_on_poor_metrics(self):
        context = {
            "strategy": "CHART_AGENT",
            "current_config": {"min_confidence": 0.55, "min_score": 2},
            "active_backtest_summary": {
                "win_rate": 0.3,
                "max_drawdown": 15,
                "blocked_entries": 0,
            },
            "sentiment": {"aggregate_score": 0},
        }
        out = _heuristic_suggestion(context)
        self.assertGreater(out["suggested_params"].get("min_confidence", 0), 0.55)


class StrategyAdvisorContextTests(unittest.TestCase):
    def setUp(self):
        from app.database import init_db
        from app.db.connection import get_connection

        db_conn._pool = None
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bots")
        cursor.execute(
            """
            INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("bot-adv-1", "CHART_AGENT", "AAPL", "1m", "STOPPED", 1000, '{"min_confidence": 0.6}'),
        )
        conn.commit()
        conn.close()

    def test_build_context_loads_bot(self):
        from app.services.bots.strategy_advisor import _load_bot

        bot = _load_bot("bot-adv-1")
        self.assertIsNotNone(bot)
        ctx = build_advisor_context(bot)
        self.assertEqual(ctx["symbol"], "AAPL")
        self.assertIn("min_confidence", ctx["current_config"])


if __name__ == "__main__":
    unittest.main()
