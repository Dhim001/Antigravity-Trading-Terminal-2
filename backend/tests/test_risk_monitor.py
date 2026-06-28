"""Drawdown kill switch and risk state tests."""

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import app.db.connection as db_conn
from app.database import init_db
from app.services.bots import risk_state_store as store
from app.services.bots.risk_monitor import RiskMonitor, compute_drawdown


class _FakeOms:
    def __init__(self, equity: float, gross: float = 0.0):
        self._equity = equity
        self._gross = gross

    def get_account_data(self):
        return {
            "balances": {"USD": {"balance": self._equity}},
            "positions": {},
        }


class RiskStateStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        db_conn.DB_PATH = os.path.join(self._tmpdir, "risk.db")
        db_conn._pool = None
        init_db()

    def test_peak_tracks_new_high(self):
        store.reset_kill_switch(current_equity=10_000)
        self.assertEqual(store.get_equity_peak(), 10_000)
        peak = store.update_peak_if_higher(12_000)
        self.assertEqual(peak, 12_000)
        self.assertEqual(store.get_equity_peak(), 12_000)

    def test_kill_switch_trip_and_reset(self):
        store.trip_kill_switch(12345.0)
        self.assertTrue(store.is_kill_switch_tripped())
        self.assertEqual(store.get_kill_switch_tripped_at(), 12345.0)
        store.reset_kill_switch(current_equity=9_500)
        self.assertFalse(store.is_kill_switch_tripped())
        self.assertEqual(store.get_equity_peak(), 9_500)


class RiskMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        db_conn.DB_PATH = os.path.join(self._tmpdir, "risk_monitor.db")
        db_conn._pool = None
        init_db()
        store.reset_kill_switch(current_equity=10_000)

    @patch("app.services.bots.risk_monitor.RISK_MAX_DRAWDOWN_PCT", 15.0)
    @patch("app.services.bots.risk_monitor.RISK_KILL_SWITCH_ENABLED", True)
    @patch("app.services.bots.risk_monitor.build_portfolio_snapshot")
    async def test_breach_stops_all_bots(self, mock_snapshot):
        from app.services.bots.portfolio_risk import PortfolioSnapshot

        mock_snapshot.return_value = PortfolioSnapshot(
            account_equity=8_000,
            gross_exposure=0,
            group_exposure={},
            symbol_exposure={},
        )
        store.set_equity_peak(10_000)

        bot_manager = MagicMock()
        bot_manager.stop_all_bots = AsyncMock(return_value=2)
        bot_manager.broadcast_cb = None
        bot_manager.list_bots_public = MagicMock(return_value=[])

        monitor = RiskMonitor()
        result = await monitor.evaluate(_FakeOms(8_000), bot_manager)

        self.assertGreaterEqual(result.current_drawdown_pct, 15.0)
        self.assertTrue(result.kill_switch_tripped)
        bot_manager.stop_all_bots.assert_awaited_once()
        self.assertTrue(store.is_kill_switch_tripped())

    @patch("app.services.bots.risk_monitor.RISK_MAX_DRAWDOWN_PCT", 15.0)
    @patch("app.services.bots.risk_monitor.build_portfolio_snapshot")
    def test_compute_drawdown_below_limit(self, mock_snapshot):
        from app.services.bots.portfolio_risk import PortfolioSnapshot

        mock_snapshot.return_value = PortfolioSnapshot(
            account_equity=9_500,
            gross_exposure=0,
            group_exposure={},
            symbol_exposure={},
        )
        store.set_equity_peak(10_000)
        snap = compute_drawdown(_FakeOms(9_500))
        self.assertEqual(snap.current_drawdown_pct, 5.0)
        self.assertFalse(snap.kill_switch_tripped)


if __name__ == "__main__":
    unittest.main()
