"""Portfolio analytics aggregation tests."""

import os
import tempfile
import unittest
import uuid
from datetime import datetime, timezone

import app.db.connection as db_conn
from app.database import get_connection, init_db
from app.services.analytics.portfolio import (
    MANUAL_STRATEGY,
    _fetch_account_exit_trades,
    collect_exit_trades,
    get_breakdown_stats,
    get_daily_pnl_calendar,
    get_pnl_distribution,
    get_portfolio_equity_curve,
)
from app.services.bots import analytics as bot_analytics


class PortfolioAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        db_conn.DB_PATH = os.path.join(self._tmpdir, "analytics.db")
        db_conn._pool = None
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        self.bot_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.bot_id, "MACD_RSI", "BTCUSDT", "5m", "RUNNING", 1000, "{}"),
        )
        conn.commit()
        conn.close()

        bot_analytics.record_trade(
            self.bot_id, "o1", "BTCUSDT", "BUY", 1.0, 100.0, is_exit=False,
        )
        bot_analytics.record_trade(
            self.bot_id, "o2", "BTCUSDT", "SELL", 1.0, 110.0, pnl=10.0, is_exit=True,
        )
        bot_analytics.record_trade(
            self.bot_id, "o3", "BTCUSDT", "SELL", 1.0, 90.0, pnl=-5.0, is_exit=True,
        )

        ts = datetime.now(timezone.utc).isoformat()
        self.account_history = {
            "trades": [
                {
                    "id": "manual-1",
                    "symbol": "ETHUSDT",
                    "side": "SELL",
                    "status": "FILLED",
                    "realized_pnl": 7.5,
                    "timestamp": ts,
                },
            ],
        }

    def test_combined_exit_trades(self):
        trades = collect_exit_trades(self.account_history, source="combined")
        self.assertEqual(len(trades), 3)
        sources = {t["source"] for t in trades}
        self.assertEqual(sources, {"bot", "account"})

    def test_equity_curve_cumulative(self):
        result = get_portfolio_equity_curve(self.account_history, source="combined")
        series = result["series"]
        self.assertEqual(len(series), 3)
        self.assertEqual(series[-1]["value"], 12.5)
        self.assertEqual(result["stats"]["total_pnl"], 12.5)

    def test_breakdown_by_strategy_includes_manual(self):
        result = get_breakdown_stats(self.account_history, "strategy", source="combined")
        keys = {r["key"] for r in result["rows"]}
        self.assertIn("MACD_RSI", keys)
        self.assertIn(MANUAL_STRATEGY, keys)

    def test_daily_calendar(self):
        result = get_daily_pnl_calendar(self.account_history, source="combined")
        self.assertTrue(len(result["days"]) >= 1)
        total = sum(d["pnl"] for d in result["days"])
        self.assertEqual(total, 12.5)

    def test_pnl_distribution_includes_skew_overlays(self):
        result = get_pnl_distribution(self.account_history, source="combined")
        self.assertGreaterEqual(len(result["bins"]), 1)
        self.assertIn("skewness", result["moments"])
        self.assertIn("excess_kurtosis", result["moments"])
        self.assertEqual(result["moments"]["n"], 3)
        self.assertTrue(len(result["density"]) >= 1)
        self.assertEqual(len(result["qq"]), 3)
        self.assertEqual(result["portfolio"]["unit"], "daily_pnl")
        self.assertIn("moments", result["portfolio"])

    def test_portfolio_daily_return_skew_uses_day_aggregates(self):
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        history = {
            "trades": [
                {
                    "id": "d1",
                    "symbol": "ETHUSDT",
                    "side": "SELL",
                    "status": "FILLED",
                    "realized_pnl": 10.0,
                    "timestamp": (now - timedelta(days=2)).isoformat(),
                },
                {
                    "id": "d2",
                    "symbol": "ETHUSDT",
                    "side": "SELL",
                    "status": "FILLED",
                    "realized_pnl": -4.0,
                    "timestamp": (now - timedelta(days=1)).isoformat(),
                },
                {
                    "id": "d3a",
                    "symbol": "ETHUSDT",
                    "side": "SELL",
                    "status": "FILLED",
                    "realized_pnl": 5.0,
                    "timestamp": now.isoformat(),
                },
                {
                    "id": "d3b",
                    "symbol": "ETHUSDT",
                    "side": "SELL",
                    "status": "FILLED",
                    "realized_pnl": 1.0,
                    "timestamp": now.isoformat(),
                },
            ],
        }
        result = get_pnl_distribution(history, source="account")
        portfolio = result["portfolio"]
        self.assertEqual(portfolio["n_days"], 3)
        self.assertEqual(portfolio["moments"]["n"], 3)
        self.assertTrue(len(portfolio["density"]) >= 1)
        self.assertEqual(len(portfolio["qq"]), 3)

    def test_source_filter_bot_only(self):
        trades = collect_exit_trades(self.account_history, source="bot")
        self.assertEqual(len(trades), 2)
        self.assertTrue(all(t["source"] == "bot" for t in trades))

    def test_account_exit_trades_use_realized_pnl_not_side(self):
        ts = datetime.now(timezone.utc).isoformat()
        history = {
            "trades": [
                {
                    "id": "buy-entry",
                    "symbol": "ETHUSDT",
                    "side": "BUY",
                    "status": "FILLED",
                    "realized_pnl": None,
                    "timestamp": ts,
                },
                {
                    "id": "sell-exit",
                    "symbol": "ETHUSDT",
                    "side": "SELL",
                    "status": "FILLED",
                    "realized_pnl": 5.0,
                    "timestamp": ts,
                },
                {
                    "id": "cover-exit",
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "status": "FILLED",
                    "realized_pnl": 10.0,
                    "timestamp": ts,
                },
            ],
        }
        exits = _fetch_account_exit_trades(history, 0.0)
        self.assertEqual(len(exits), 2)
        pnls = sorted(t["pnl"] for t in exits)
        self.assertEqual(pnls, [5.0, 10.0])

    def test_correlation_respects_symbol_universe(self):
        from app.services.analytics.portfolio import get_correlation_matrix

        result = get_correlation_matrix(
            self.account_history,
            source="combined",
            symbols=["BTCUSDT"],
        )
        self.assertEqual(result["symbols"], ["BTCUSDT"])


if __name__ == "__main__":
    unittest.main()
