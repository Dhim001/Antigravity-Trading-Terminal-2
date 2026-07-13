"""Unit tests for TRADE_COPILOT."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Isolate SQLite before importing app modules that touch DB.
_TEST_DIR = tempfile.mkdtemp(prefix="copilot_test_")
_TEST_DB = os.path.join(_TEST_DIR, "copilot.db")
os.environ["DB_PATH"] = _TEST_DB

import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = _TEST_DB

from app.database import init_db  # noqa: E402

init_db()

from app.services.agent.copilot import (  # noqa: E402
    classify_intent,
    confirm_action,
    extract_allocation,
    extract_days,
    extract_symbol,
    extract_strategy,
    fuzzy_resolve_symbol,
    normalize_symbol,
    recommend_strategy_for_regime,
    handle_message,
    pop_pending,
    _SESSION_MEMORY,
)
from app.services.agent import copilot_store  # noqa: E402


class ClassifyTests(unittest.TestCase):
    def test_fuzzy_btcustd(self):
        self.assertEqual(fuzzy_resolve_symbol("BTCUSTD"), "BTCUSDT")
        self.assertEqual(extract_symbol("what is BTCUSTD doing now?"), "BTCUSDT")
        self.assertEqual(normalize_symbol("BTCUSTD"), "BTCUSDT")

    def test_doing_now_is_analysis_not_help(self):
        intent, hint = classify_intent("what is BTCUSTD doing now?")
        self.assertEqual(intent, "analysis")
        self.assertEqual(hint, "analyze_symbol")
        intent2, hint2 = classify_intent("what is BTCUSDT doing now?")
        self.assertEqual(intent2, "analysis")
        self.assertEqual(hint2, "analyze_symbol")

    def test_help_still_help(self):
        intent, hint = classify_intent("help")
        self.assertEqual(intent, "help")
        self.assertEqual(hint, "help")

    def test_ambiguous_is_clarify_not_help_menu(self):
        intent, hint = classify_intent("asdf qwerty zxcv")
        self.assertEqual(intent, "help")
        self.assertEqual(hint, "clarify")

    def test_portfolio_query(self):
        intent, hint = classify_intent("What's my total exposure right now?")
        self.assertEqual(intent, "query")
        self.assertEqual(hint, "get_portfolio_status")

    def test_deploy_action(self):
        intent, hint = classify_intent("Deploy a CHART_AGENT on ETHUSDT with $2000")
        self.assertEqual(intent, "action")
        self.assertEqual(hint, "deploy_bot")

    def test_recommend_bot_for_ranging_not_blind_deploy(self):
        msg = "what is the right bot to deploy for ADAUSDT still is in a ranging market?"
        intent, hint = classify_intent(msg)
        self.assertEqual(intent, "analysis")
        self.assertEqual(hint, "recommend_strategy")
        rec = recommend_strategy_for_regime("ranging")
        self.assertEqual(rec["primary"], "BRS_SCALPING")
        self.assertIn("CHART_AGENT", rec["avoid"])

    def test_extract_brs_strategy(self):
        self.assertEqual(extract_strategy("Deploy BRS_SCALPING on ADA"), "BRS_SCALPING")
        self.assertIsNone(extract_strategy("deploy something", default=None))

    def test_explain(self):
        intent, hint = classify_intent("Why did my last BTC trade lose money?")
        self.assertEqual(intent, "explain")
        self.assertEqual(hint, "explain_trade")

    def test_analyze(self):
        intent, hint = classify_intent("What does AAPL look like right now?")
        self.assertEqual(intent, "analysis")
        self.assertEqual(hint, "analyze_symbol")

    def test_backtest_not_chart_analysis(self):
        """'CHART_AGENT' must not steal routing into analyze_symbol."""
        intent, hint = classify_intent(
            "Run a 90-day backtest on CHART_AGENT for BTC?"
        )
        self.assertEqual(intent, "analysis")
        self.assertEqual(hint, "run_backtest")

    def test_extract_days_and_btc_normalize(self):
        self.assertEqual(extract_days("Run a 90-day backtest", default=7), 90)
        self.assertEqual(extract_days("backtest 30d", default=7), 30)
        self.assertEqual(normalize_symbol("BTC"), "BTCUSDT")
        self.assertEqual(normalize_symbol("btcusdt"), "BTCUSDT")
        self.assertEqual(normalize_symbol("AAPL"), "AAPL")

    def test_what_about_symbol_is_analysis(self):
        intent, hint = classify_intent("what about BTCUSDT?")
        self.assertEqual(intent, "analysis")
        self.assertEqual(hint, "analyze_symbol")
        intent2, hint2 = classify_intent("how about ETH?")
        self.assertEqual(intent2, "analysis")
        self.assertEqual(hint2, "analyze_symbol")

    def test_portfolio_still_routes(self):
        intent, hint = classify_intent("What's my total exposure right now?")
        self.assertEqual(hint, "get_portfolio_status")

    def test_what_market_is_symbol(self):
        intent, hint = classify_intent("what market is ETHUSDT in?")
        self.assertEqual(intent, "analysis")
        self.assertEqual(hint, "analyze_symbol")

    def test_meta_timeframe_before_market_keyword(self):
        """Follow-ups about timeframe must not re-run analyze_symbol."""
        msg = "what timeframe was used for the ETHUSDT market direction outcome?"
        intent, hint = classify_intent(msg)
        self.assertEqual(intent, "analysis")
        self.assertEqual(hint, "meta_insight")
        intent2, hint2 = classify_intent("what ADX threshold did you use?")
        self.assertEqual(hint2, "meta_insight")

    def test_change_timeframe_routes_to_analyze(self):
        intent, hint = classify_intent("change timeframe to 5m for ETHUSDT")
        self.assertEqual(hint, "analyze_symbol")
        intent2, hint2 = classify_intent("what market is ETHUSDT in on 5m?")
        self.assertEqual(hint2, "analyze_symbol")

    def test_chart_analysis_phrasing(self):
        """Chart-copilot prompts should route to analyze_symbol."""
        for msg in (
            "What does BTCUSDT look like right now?",
            "Analyze ETHUSDT on the chart",
            "Is AAPL trending or ranging?",
            "chart regime for SOLUSDT",
            "what market is ETHUSDT in?",
        ):
            intent, hint = classify_intent(msg)
            self.assertEqual(intent, "analysis", msg)
            self.assertEqual(hint, "analyze_symbol", msg)

    def test_bot_performance_phrasing(self):
        for msg in (
            "how are the bot doing?",
            "How are my bots doing?",
            "bots doing ok?",
        ):
            intent, hint = classify_intent(msg)
            self.assertEqual(intent, "query", msg)
            self.assertEqual(hint, "get_bot_performance", msg)

    def test_list_bots(self):
        intent, hint = classify_intent("show my bots")
        self.assertEqual(hint, "list_bots")

    def test_extract_symbol_and_alloc(self):
        self.assertEqual(extract_symbol("Deploy on ETHUSDT please"), "ETHUSDT")
        self.assertEqual(extract_allocation("with $2000 allocation"), 2000.0)

    def test_extract_symbol_from_chart_prompt(self):
        self.assertEqual(extract_symbol("What does BTCUSDT look like right now?"), "BTCUSDT")
        self.assertEqual(extract_symbol("Analyze ETHUSDT please"), "ETHUSDT")


class CopilotHandleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = MagicMock()
        self.state.oms = MagicMock()
        self.state.oms.get_account_data.return_value = {
            "balances": {"USD": {"balance": 10000}},
            "positions": {},
        }
        self.state.bot_manager = MagicMock()
        self.state.bot_manager.active_bots = {}
        self.state.bot_manager.list_bots_public.return_value = []
        self.state.bot_manager.create_bot = AsyncMock(return_value="bot-xyz")
        self.state.chart_analyst = None

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    @patch("app.services.agent.copilot._tool_portfolio")
    async def test_query_portfolio(self, mock_port):
        mock_port.return_value = {
            "account_equity": 10000,
            "gross_exposure": 1500,
            "group_exposure": {},
            "symbol_exposure": {},
        }
        res = await handle_message(self.state, "How is my portfolio?")
        self.assertTrue(res.ok)
        self.assertEqual(res.intent, "query")
        self.assertFalse(res.requires_confirmation)
        self.assertIn("Portfolio", res.reply)

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    @patch("app.services.agent.copilot._tool_run_backtest", new_callable=AsyncMock)
    async def test_run_backtest_capability(self, mock_bt):
        mock_bt.return_value = {
            "symbol": "BTCUSDT",
            "strategy": "CHART_AGENT",
            "days": 90,
            "timeframe": "1m",
            "bar_count": 12000,
            "win_rate": 55.0,
            "total_pnl": 240.5,
            "max_drawdown": 8.2,
            "trade_count": 42,
            "return_pct": 24.1,
            "sharpe_ratio": 1.3,
        }
        res = await handle_message(
            self.state,
            "Run a 90-day backtest on CHART_AGENT for BTC?",
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.intent, "analysis")
        mock_bt.assert_awaited()
        kwargs = mock_bt.await_args
        self.assertEqual(kwargs.args[1], "BTCUSDT")
        self.assertEqual(kwargs.args[2], "CHART_AGENT")
        self.assertEqual(kwargs.args[3], 90)
        self.assertIn("Backtest", res.reply)
        self.assertIn("BTCUSDT", res.reply)
        self.assertIn("CHART_AGENT", res.reply)
        self.assertNotIn("could not be completed", res.reply.lower())
        self.assertNotIn("agent must be enabled", res.reply.lower())

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    @patch("app.services.agent.copilot._tool_run_backtest", new_callable=AsyncMock)
    async def test_run_backtest_queued_reply(self, mock_bt):
        mock_bt.return_value = {
            "queued": True,
            "job_id": "job-abc",
            "symbol": "BTCUSDT",
            "strategy": "CHART_AGENT",
            "days": 90,
            "timeframe": "1m",
            "estimated_sec": 420,
            "message": "Queued",
        }
        res = await handle_message(
            self.state,
            "Run a 90-day backtest on CHART_AGENT for BTC?",
        )
        self.assertTrue(res.ok)
        self.assertIn("queued", res.reply.lower())
        self.assertIn("job-abc", res.reply)
        self.assertIn("Algo", res.reply)

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    async def test_chart_analyze_capability(self):
        """Example: chart-copilot path — user asks what a symbol looks like.

        Flow under test:
          1) intent → analysis / analyze_symbol
          2) chart_analyst.analyze(symbol, ...) is called
          3) reply is formatted (signal / score / confidence / reasons)
        """
        insight = MagicMock()
        insight.to_dict.return_value = {
            "signal": "BUY",
            "score": 3,
            "confidence": 0.72,
            "reasons": [
                "EMA stack bullish",
                "ADX > 25",
                "Higher highs on 1m",
            ],
            "sub_reports": {
                "trend": {"score": 2, "reasons": ["EMA stack bullish"], "trend_regime": "trending"},
                "risk": {"atr_regime": "normal", "score": 0},
                "regime_weights": {"regime": "trending", "weights": {}},
            },
        }
        self.state.chart_analyst = MagicMock()
        self.state.chart_analyst.analyze = AsyncMock(return_value=insight)

        res = await handle_message(
            self.state,
            "What does BTCUSDT look like right now?",
            active_symbol="ETHUSDT",
        )

        self.assertTrue(res.ok)
        self.assertEqual(res.intent, "analysis")
        self.assertFalse(res.requires_confirmation)

        self.state.chart_analyst.analyze.assert_awaited()
        call_kwargs = self.state.chart_analyst.analyze.await_args
        # Symbol from the prompt wins over active_symbol
        self.assertEqual(call_kwargs.args[0], "BTCUSDT")

        tool = next(t for t in res.tool_results if t["tool"] == "analyze_symbol")
        self.assertEqual(tool["result"]["signal"], "BUY")
        self.assertEqual(tool["result"]["score"], 3)
        self.assertAlmostEqual(tool["result"]["confidence"], 0.72)
        self.assertEqual(tool["result"]["trend_regime"], "trending")
        self.assertEqual(tool["result"]["market_regime"], "trending")

        # Template reply should be human-readable snapshot, not raw JSON / ADX essay
        self.assertIn("BTCUSDT", res.reply)
        self.assertIn("BUY", res.reply)
        self.assertIn("EMA stack bullish", res.reply)
        self.assertNotIn('"signal"', res.reply)
        self.assertNotIn("Based on ADX (trending if ADX >", res.reply)
        self.assertIn("_Details:", res.reply)

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    async def test_btcustd_doing_now_analyzes_not_help(self):
        insight = MagicMock()
        insight.to_dict.return_value = {
            "signal": "HOLD",
            "score": 0,
            "confidence": 0.4,
            "reasons": ["ADX flat"],
            "sub_reports": {
                "trend": {"score": 0, "reasons": [], "trend_regime": "ranging"},
                "risk": {"atr_regime": "normal", "score": 0},
            },
        }
        self.state.chart_analyst = MagicMock()
        self.state.chart_analyst.analyze = AsyncMock(return_value=insight)

        res = await handle_message(
            self.state,
            "what is BTCUSTD doing now?",
            active_symbol="ETHUSDT",
        )

        self.assertTrue(res.ok)
        self.assertEqual(res.intent, "analysis")
        self.assertNotIn("I'm **TRADE_COPILOT**. Try", res.reply)
        self.assertNotIn("ask in plain English", res.reply.lower())
        self.assertNotIn("Based on ADX (trending if ADX >", res.reply)
        self.assertNotIn("Directional signal (separate from regime)", res.reply)
        self.assertIn("BTCUSDT", res.reply)
        self.assertIn("ranging", res.reply.lower())
        self.assertIn("_Details:", res.reply)
        self.state.chart_analyst.analyze.assert_awaited()
        self.assertEqual(self.state.chart_analyst.analyze.await_args.args[0], "BTCUSDT")

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", True)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    async def test_trending_or_ranging_answers_regime(self):
        """Regime Q must lead with trending/ranging from ADX trend_regime, not signal fluff."""
        insight = MagicMock()
        insight.to_dict.return_value = {
            "signal": "NONE",
            "score": 1,
            "confidence": 0.17,
            "reasons": [
                "Price above EMA21",
                "RSI neutral",
                "MACD above signal",
                "normal volume",
                "neutral news sentiment",
            ],
            "sub_reports": {
                "trend": {
                    "score": 1,
                    "reasons": ["Price above EMA21"],
                    "trend_regime": "ranging",
                },
                "risk": {"atr_regime": "normal", "score": 0},
                "regime_weights": {"regime": "ranging", "weights": {}},
            },
        }
        self.state.chart_analyst = MagicMock()
        self.state.chart_analyst.analyze = AsyncMock(return_value=insight)

        res = await handle_message(
            self.state,
            "Is ETH in a trending or ranging market?",
            active_symbol="BTCUSDT",
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.intent, "analysis")
        self.state.chart_analyst.analyze.assert_awaited()
        self.assertEqual(self.state.chart_analyst.analyze.await_args.args[0], "ETHUSDT")

        tool = next(t for t in res.tool_results if t["tool"] == "analyze_symbol")
        self.assertEqual(tool["result"]["trend_regime"], "ranging")
        self.assertEqual(tool["result"]["market_regime"], "ranging")
        self.assertIsNone(tool["result"].get("error"))

        reply_l = res.reply.lower()
        self.assertIn("ranging", reply_l)
        self.assertIn("ethusdt", reply_l)
        # Must not be the old vague reasons-only dump without a regime verdict.
        self.assertIn("trending if adx", reply_l)
        self.assertNotIn("mixed signals", reply_l)

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    async def test_what_about_followup_analyzes_symbol(self):
        """'what about BTCUSDT?' must analyze, not dump portfolio status."""
        insight = MagicMock()
        insight.to_dict.return_value = {
            "signal": "NONE",
            "score": 0,
            "confidence": 0.2,
            "reasons": ["RSI neutral"],
            "sub_reports": {
                "trend": {"trend_regime": "trending", "score": 1, "reasons": []},
                "risk": {"atr_regime": "normal"},
                "regime_weights": {"regime": "trending"},
            },
        }
        self.state.chart_analyst = MagicMock()
        self.state.chart_analyst.analyze = AsyncMock(return_value=insight)

        sid = copilot_store.ensure_session_id(None)
        copilot_store.append_message(
            sid, "user", "Is ETH in a trending or ranging market?", intent="analysis"
        )

        res = await handle_message(
            self.state,
            "what about BTCUSDT?",
            session_id=sid,
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.intent, "analysis")
        self.state.chart_analyst.analyze.assert_awaited()
        self.assertEqual(self.state.chart_analyst.analyze.await_args.args[0], "BTCUSDT")
        self.assertNotIn("Portfolio status", res.reply)
        self.assertIn("TRENDING", res.reply.upper())
        self.assertIn("BTCUSDT", res.reply)

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    async def test_ranging_deploy_advice_recommends_brs(self):
        insight = MagicMock()
        insight.to_dict.return_value = {
            "signal": "NONE",
            "score": 0,
            "confidence": 0.2,
            "reasons": ["Chop"],
            "sub_reports": {
                "trend": {"trend_regime": "ranging", "score": 0, "reasons": []},
                "risk": {"atr_regime": "normal"},
                "regime_weights": {"regime": "ranging"},
            },
        }
        self.state.chart_analyst = MagicMock()
        self.state.chart_analyst.analyze = AsyncMock(return_value=insight)

        res = await handle_message(
            self.state,
            "what is the right bot to deploy for ADAUSDT still is in a ranging market?",
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.intent, "analysis")
        self.assertFalse(res.requires_confirmation)
        self.assertIsNone(res.pending_id)
        tool = next(t for t in res.tool_results if t["tool"] == "recommend_strategy")
        self.assertEqual(tool["result"]["primary"], "BRS_SCALPING")
        self.assertEqual(tool["result"]["symbol"], "ADAUSDT")
        self.assertIn("BRS_SCALPING", res.reply)
        self.assertNotIn("Confirm action", res.reply)
        self.assertNotIn("CHART_AGENT", res.reply.split("Avoid")[0])  # not primary

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    async def test_what_market_answers_regime(self):
        insight = MagicMock()
        insight.to_dict.return_value = {
            "signal": "NONE",
            "score": 1,
            "confidence": 0.2,
            "reasons": ["ADX soft"],
            "sub_reports": {
                "trend": {"trend_regime": "trending", "score": 1, "reasons": []},
                "risk": {"atr_regime": "normal"},
                "regime_weights": {"regime": "trending"},
            },
        }
        self.state.chart_analyst = MagicMock()
        self.state.chart_analyst.analyze = AsyncMock(return_value=insight)

        res = await handle_message(self.state, "what market is ETHUSDT in?")
        self.assertTrue(res.ok)
        self.assertEqual(res.intent, "analysis")
        self.assertNotIn("TRADE_COPILOT", res.reply)
        self.assertIn("TRENDING", res.reply.upper())
        self.assertIn("ETHUSDT", res.reply)

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    async def test_meta_timeframe_followup_not_full_analysis(self):
        """After a regime ask, timeframe follow-up must answer 1m — not re-dump TRENDING."""
        _SESSION_MEMORY.clear()
        insight = MagicMock()
        insight.to_dict.return_value = {
            "signal": "BUY",
            "score": 3,
            "confidence": 0.7,
            "reasons": ["EMA stack"],
            "sub_reports": {
                "trend": {"trend_regime": "trending", "score": 2, "reasons": []},
                "risk": {"atr_regime": "normal"},
                "regime_weights": {"regime": "trending"},
            },
        }
        self.state.chart_analyst = MagicMock()
        self.state.chart_analyst.analyze = AsyncMock(return_value=insight)

        sid = "test-meta-tf-session"
        first = await handle_message(
            self.state, "what market is ETHUSDT in?", session_id=sid
        )
        self.assertTrue(first.ok)
        self.assertIn("TRENDING", first.reply.upper())
        self.assertIn("1m", first.reply)  # provenance on analyze
        self.assertEqual(self.state.chart_analyst.analyze.await_count, 1)

        follow = await handle_message(
            self.state,
            "what timeframe was used for the ETHUSDT market direction outcome?",
            session_id=sid,
        )
        self.assertTrue(follow.ok)
        self.assertEqual(follow.tool_results[0]["tool"], "meta_insight")
        self.assertEqual(self.state.chart_analyst.analyze.await_count, 1)  # no re-analyze
        self.assertIn("1m", follow.reply)
        self.assertIn("Timeframe", follow.reply)
        # Must not re-lead with full regime dump.
        self.assertNotIn("is in a TRENDING market", follow.reply)
        self.assertNotIn("Directional signal", follow.reply)
        self.assertNotRegex(follow.reply, r"(?i)\*\*[^*]*TRENDING[^*]*\*\*")  # no regime headline

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    async def test_analyze_respects_5m_timeframe(self):
        _SESSION_MEMORY.clear()
        insight = MagicMock()
        insight.to_dict.return_value = {
            "signal": "NONE",
            "score": 1,
            "confidence": 0.2,
            "reasons": [],
            "sub_reports": {
                "trend": {"trend_regime": "ranging", "score": 0, "reasons": []},
                "regime_weights": {"regime": "ranging"},
            },
        }
        self.state.chart_analyst = MagicMock()
        self.state.chart_analyst.analyze = AsyncMock(return_value=insight)

        res = await handle_message(
            self.state,
            "what market is ETHUSDT in on 5m?",
            session_id="tf-5m-session",
        )
        self.assertTrue(res.ok)
        self.state.chart_analyst.analyze.assert_awaited()
        kwargs = self.state.chart_analyst.analyze.await_args.kwargs
        self.assertEqual(kwargs.get("timeframe"), "5m")
        self.assertEqual(res.tool_results[0]["result"]["timeframe"], "5m")
        self.assertIn("5m", res.reply)

        # Session remembers 5m for follow-up without repeating TF
        res2 = await handle_message(
            self.state,
            "change timeframe to 5m",
            session_id="tf-5m-session",
        )
        self.assertTrue(res2.ok)
        self.assertEqual(
            self.state.chart_analyst.analyze.await_args.kwargs.get("timeframe"),
            "5m",
        )
        self.assertEqual(res2.tool_results[0]["result"]["symbol"], "ETHUSDT")

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", True)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    @patch("app.services.agent.copilot_agent.plan_tool_calls", new_callable=AsyncMock)
    async def test_agent_planner_passes_5m(self, mock_plan):
        """LLM planner chooses analyze_symbol with timeframe=5m."""
        _SESSION_MEMORY.clear()
        mock_plan.return_value = {
            "tool_calls": [{
                "name": "analyze_symbol",
                "arguments": {"symbol": "ETHUSDT", "timeframe": "5m"},
            }],
            "direct_reply": None,
        }
        insight = MagicMock()
        insight.to_dict.return_value = {
            "signal": "HOLD",
            "score": 0,
            "confidence": 0.3,
            "reasons": [],
            "sub_reports": {
                "trend": {"trend_regime": "trending", "score": 1, "reasons": []},
                "regime_weights": {"regime": "trending"},
            },
        }
        self.state.chart_analyst = MagicMock()
        self.state.chart_analyst.analyze = AsyncMock(return_value=insight)

        res = await handle_message(
            self.state,
            "change timeframe to 5m and tell me what market ETH is in",
            session_id="agent-5m",
        )
        self.assertTrue(res.ok)
        mock_plan.assert_awaited()
        self.assertEqual(
            self.state.chart_analyst.analyze.await_args.kwargs.get("timeframe"),
            "5m",
        )
        self.assertIn("5m", res.reply)

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    async def test_chart_analyze_falls_back_to_active_symbol(self):
        """If the prompt has no ticker, use the UI active_symbol."""
        insight = MagicMock()
        insight.to_dict.return_value = {
            "signal": "HOLD",
            "score": 0,
            "confidence": 0.4,
            "reasons": ["Chop"],
            "sub_reports": {
                "trend": {"trend_regime": "ranging", "score": 0, "reasons": ["Chop"]},
                "regime_weights": {"regime": "ranging"},
            },
        }
        self.state.chart_analyst = MagicMock()
        self.state.chart_analyst.analyze = AsyncMock(return_value=insight)

        res = await handle_message(
            self.state,
            "What does the chart look like right now?",
            active_symbol="AAPL",
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.intent, "analysis")
        self.state.chart_analyst.analyze.assert_awaited()
        self.assertEqual(self.state.chart_analyst.analyze.await_args.args[0], "AAPL")
        self.assertIn("AAPL", res.reply)

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    async def test_chart_analyze_unavailable(self):
        self.state.chart_analyst = None
        res = await handle_message(self.state, "Analyze BTCUSDT")
        self.assertTrue(res.ok)
        self.assertIn("unavailable", res.reply.lower())

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    @patch("app.services.agent.copilot._tool_bot_performance")
    async def test_bot_performance_reply_formatted(self, mock_perf):
        mock_perf.return_value = {
            "rankings": {
                "top": [
                    {
                        "symbol": "ETHUSDT",
                        "strategy": "CHART_AGENT",
                        "status": "RUNNING",
                        "total_pnl": 12.5,
                        "win_rate": 60.0,
                        "exit_count": 5,
                    }
                ],
                "total_bots": 1,
            },
            "active_bots": {
                "bots": [
                    {
                        "symbol": "ETHUSDT",
                        "strategy": "CHART_AGENT",
                        "status": "RUNNING",
                        "allocation": 1000,
                        "total_pnl": 12.5,
                    }
                ],
                "count": 1,
            },
        }
        res = await handle_message(self.state, "how are the bot doing?")
        self.assertTrue(res.ok)
        self.assertEqual(res.intent, "query")
        self.assertIn("Bot performance", res.reply)
        self.assertIn("ETHUSDT", res.reply)
        self.assertNotIn("open_bot_positions", res.reply)

    @patch("app.services.agent.copilot.TRADE_COPILOT_USE_LLM", False)
    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", True)
    async def test_deploy_requires_confirm(self):
        res = await handle_message(
            self.state,
            "Deploy CHART_AGENT on ETHUSDT with $2000",
        )
        self.assertTrue(res.ok)
        self.assertTrue(res.requires_confirmation)
        self.assertIsNotNone(res.pending_id)
        self.assertEqual(res.pending_action["type"], "deploy_bot")
        self.assertEqual(res.pending_action["params"]["symbol"], "ETHUSDT")
        self.assertEqual(res.pending_action["params"]["allocation"], 2000.0)

        confirmed = await confirm_action(self.state, res.pending_id)
        self.assertTrue(confirmed["ok"])
        self.state.bot_manager.create_bot.assert_awaited()
        self.assertIsNone(pop_pending(res.pending_id))

    @patch("app.services.agent.copilot.TRADE_COPILOT_ENABLED", False)
    async def test_disabled(self):
        res = await handle_message(self.state, "hello")
        self.assertFalse(res.ok)

    def test_store_roundtrip(self):
        sid = copilot_store.ensure_session_id(None)
        copilot_store.append_message(sid, "user", "hi")
        copilot_store.append_message(sid, "assistant", "hello", intent="help")
        msgs = copilot_store.list_messages(sid, limit=10)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["role"], "user")
        deleted = copilot_store.clear_session(sid)
        self.assertGreaterEqual(deleted, 1)


if __name__ == "__main__":
    unittest.main()
