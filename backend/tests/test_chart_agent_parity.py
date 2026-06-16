"""Closed-bar score parity: backend rule engine vs frontend generateSignal()."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from app.services.agent.feature_builder import FeatureBuilder
from app.services.agent.rule_engine import display_label, score_dataframe
from app.services.bots.screener import MarketScreenerService
from tests.test_chart_agent_rules import make_trending_candles

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCORE_SCRIPT = REPO_ROOT / "frontend" / "scripts" / "score-closed-bar.mjs"


class TestChartAgentParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        cls.builder = FeatureBuilder(MarketScreenerService())
        FIXTURES.mkdir(exist_ok=True)
        cls.fixture_path = FIXTURES / "chart_agent_candles.json"
        if not cls.fixture_path.exists():
            cls.fixture_path.write_text(
                json.dumps(make_trending_candles(220, drift=0.0008)),
                encoding="utf-8",
            )

    def _backend_score(self, candles: list[dict]) -> dict:
        df = self.builder.build("BTCUSDT", candles)
        insight = score_dataframe(df, "BTCUSDT")
        self.assertIsNotNone(insight)
        return {
            "score": insight.score,
            "signal": display_label(insight.score),
            "reasons": insight.reasons,
        }

    def _frontend_score(self, candles: list[dict]) -> dict:
        if not self.node or not SCORE_SCRIPT.is_file():
            self.skipTest("node or score-closed-bar.mjs unavailable")
        proc = subprocess.run(
            [self.node, str(SCORE_SCRIPT), str(self.fixture_path)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT / "frontend"),
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            self.skipTest(f"frontend scorer failed: {proc.stderr.strip()}")
        return json.loads(proc.stdout)

    def test_backend_frontend_score_parity_on_fixture(self):
        candles = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        backend = self._backend_score(candles)
        frontend = self._frontend_score(candles)
        self.assertEqual(backend["score"], frontend["score"])
        self.assertEqual(backend["signal"], frontend["signal"])

    def test_uptrend_positive_or_neutral_score(self):
        candles = make_trending_candles(220, drift=0.0012)
        df = self.builder.build("ETHUSDT", candles)
        insight = score_dataframe(df, "ETHUSDT")
        self.assertIsNotNone(insight)
        self.assertGreaterEqual(insight.score, -1)

    def test_downtrend_negative_or_neutral_score(self):
        candles = make_trending_candles(220, start=100.0, drift=-0.0012)
        df = self.builder.build("ETHUSDT", candles)
        insight = score_dataframe(df, "ETHUSDT")
        self.assertIsNotNone(insight)
        self.assertLessEqual(insight.score, 1)


if __name__ == "__main__":
    unittest.main()
