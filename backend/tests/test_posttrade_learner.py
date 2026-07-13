"""Unit tests for the Post-Trade Learning Agent."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.bots.posttrade_learner import (
    TradeLesson,
    build_config_patch,
    classify_outcome,
    compute_mae_mfe,
    learn_from_closed_trade,
    template_lesson,
)


class MaeMfeTests(unittest.TestCase):
    def test_long_excursion(self):
        mae, mfe = compute_mae_mfe(
            entry_price=100.0,
            is_long=True,
            high_watermark=105.0,
            low_watermark=97.0,
        )
        self.assertAlmostEqual(mfe, 5.0)
        self.assertAlmostEqual(mae, 3.0)

    def test_short_excursion(self):
        mae, mfe = compute_mae_mfe(
            entry_price=100.0,
            is_long=False,
            high_watermark=103.0,
            low_watermark=94.0,
        )
        self.assertAlmostEqual(mfe, 6.0)
        self.assertAlmostEqual(mae, 3.0)


class ClassifyOutcomeTests(unittest.TestCase):
    def test_stop_too_tight(self):
        cls, reason = classify_outcome(
            pnl=-50.0,
            mae_pct=1.4,
            mfe_pct=0.2,
            trigger_type="SL",
            insight={},
            stop_loss_percent=1.5,
        )
        self.assertEqual(cls, "stop_too_tight")
        self.assertIn("note", reason)

    def test_good_entry_bad_exit(self):
        cls, _ = classify_outcome(
            pnl=-20.0,
            mae_pct=1.0,
            mfe_pct=2.5,
            trigger_type="SIGNAL",
            insight={},
        )
        self.assertEqual(cls, "good_entry_bad_exit")

    def test_regime_mismatch(self):
        cls, _ = classify_outcome(
            pnl=-10.0,
            mae_pct=0.5,
            mfe_pct=0.4,
            trigger_type="SIGNAL",
            insight={"regime": "ranging"},
        )
        self.assertEqual(cls, "regime_mismatch")

    def test_clean_win(self):
        cls, _ = classify_outcome(
            pnl=40.0,
            mae_pct=0.5,
            mfe_pct=2.0,
            trigger_type="TP",
            insight={},
        )
        self.assertEqual(cls, "clean_win")


class BuildPatchTests(unittest.TestCase):
    def test_stop_too_tight_widens_stop(self):
        patch = build_config_patch(
            "stop_too_tight",
            {"stop_loss_percent": 1.5, "min_confidence": 0.55},
            strategy="CHART_AGENT",
        )
        self.assertIn("stop_loss_percent", patch)
        self.assertGreater(patch["stop_loss_percent"], 1.5)

    def test_regime_mismatch_blocks_ranging(self):
        patch = build_config_patch(
            "regime_mismatch",
            {"min_confidence": 0.55},
            strategy="CHART_AGENT",
        )
        self.assertTrue(patch.get("block_ranging_markets"))
        self.assertGreater(patch.get("min_confidence", 0), 0.55)


class LearnFromClosedTradeTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.services.bots.posttrade_learner.POSTTRADE_LEARNER_ENABLED", False)
    async def test_disabled(self):
        result = await learn_from_closed_trade(
            None,
            "bot-1",
            symbol="AAPL",
            exit_side="SELL",
            exit_price=100.0,
            entry_price=99.0,
            quantity=1.0,
            pnl=1.0,
        )
        self.assertEqual(result.outcome_class, "disabled")

    @patch("app.services.bots.posttrade_learner.emit_notification", new_callable=AsyncMock)
    @patch("app.services.bots.posttrade_learner.upsert_entry", return_value={"id": "j1"})
    @patch("app.services.bots.posttrade_learner.get_aggregate_sentiment", return_value={})
    @patch("app.services.bots.posttrade_learner.fetch_entry_context", return_value={
        "side": "BUY",
        "price": 100.0,
        "insight_snapshot": {"confidence": 0.7, "signal": "BUY"},
    })
    @patch("app.services.bots.posttrade_learner._llm_lesson", new_callable=AsyncMock, return_value=None)
    @patch("app.services.bots.posttrade_learner.POSTTRADE_LEARNER_AUTO_APPLY", False)
    @patch("app.services.bots.posttrade_learner.POSTTRADE_LEARNER_AUTO_RETRAIN", False)
    @patch("app.services.bots.posttrade_learner.POSTTRADE_LEARNER_ENABLED", True)
    async def test_learn_writes_journal(self, *_mocks):
        mgr = MagicMock()
        mgr._get_bot_dict.return_value = {
            "id": "bot-1",
            "symbol": "AAPL",
            "strategy": "CHART_AGENT",
            "config": {"stop_loss_percent": 1.5, "min_confidence": 0.55},
        }
        mgr.log_bot_event = AsyncMock()
        mgr.update_bot_config = AsyncMock()

        result = await learn_from_closed_trade(
            mgr,
            "bot-1",
            symbol="AAPL",
            exit_side="SELL",
            exit_price=98.5,
            entry_price=100.0,
            quantity=10.0,
            pnl=-15.0,
            trigger_type="SL",
            high_watermark=100.3,
            low_watermark=98.5,
        )
        self.assertIsInstance(result, TradeLesson)
        self.assertEqual(result.outcome_class, "stop_too_tight")
        self.assertTrue(result.lesson)
        self.assertEqual(result.journal_id, "j1")
        self.assertIn("stop_loss_percent", result.config_patch)
        self.assertFalse(result.applied)
        mgr.log_bot_event.assert_awaited()

    def test_template_lesson(self):
        text = template_lesson(
            "clean_win",
            symbol="NVDA",
            pnl=12.5,
            mae_pct=0.4,
            mfe_pct=2.1,
            patch={},
            reason={"note": "Solid winner"},
        )
        self.assertIn("NVDA", text)
        self.assertIn("clean_win", text)


if __name__ == "__main__":
    unittest.main()
