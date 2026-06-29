"""Drawdown kill switch and risk state tests."""

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import app.db.connection as db_conn
from app.database import init_db
from app.services.bots import risk_state_store as store
from app.services.bots.risk_monitor import RiskMonitor, compute_drawdown
import app.services.bots.risk_monitor as risk_monitor_mod


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
        # Always reset the breach counter between tests
        risk_monitor_mod._breach_counter = 0

    def _make_bot_manager(self):
        bm = MagicMock()
        bm.stop_all_bots = AsyncMock(return_value=2)
        bm.broadcast_cb = None
        bm.list_bots_public = MagicMock(return_value=[])
        bm.flatten_weekend_non_crypto_positions = AsyncMock(return_value=0)
        bm.close_stale_positions = AsyncMock(return_value=0)
        return bm

    @patch("app.services.bots.risk_monitor.RISK_MAX_DRAWDOWN_PCT", 15.0)
    @patch("app.services.bots.risk_monitor.RISK_KILL_SWITCH_ENABLED", True)
    @patch("app.services.bots.risk_monitor.RISK_WEEKEND_FLATTEN_ENABLED", False)
    @patch("app.services.bots.risk_monitor.RISK_POSITION_DURATION_ENABLED", False)
    @patch("app.services.bots.risk_monitor.RISK_DYNAMIC_CORRELATION_ENABLED", False)
    @patch("app.services.bots.risk_monitor.build_portfolio_snapshot")
    async def test_breach_requires_consecutive_ticks(self, mock_snapshot):
        """Kill switch must NOT trip on a single breach tick — must persist
        for _BREACH_CONFIRM_TICKS consecutive evaluations."""
        from app.services.bots.portfolio_risk import PortfolioSnapshot

        mock_snapshot.return_value = PortfolioSnapshot(
            account_equity=8_000,
            gross_exposure=0,
            group_exposure={},
            symbol_exposure={},
        )
        store.set_equity_peak(10_000)

        bot_manager = self._make_bot_manager()
        monitor = RiskMonitor()

        # Tick 1 — breach detected but NOT confirmed yet
        result = await monitor.evaluate(_FakeOms(8_000), bot_manager)
        self.assertGreaterEqual(result.current_drawdown_pct, 15.0)
        self.assertFalse(result.kill_switch_tripped)
        bot_manager.stop_all_bots.assert_not_awaited()

        # Tick 2 — still breaching, still not confirmed
        result = await monitor.evaluate(_FakeOms(8_000), bot_manager)
        self.assertFalse(result.kill_switch_tripped)
        bot_manager.stop_all_bots.assert_not_awaited()

        # Tick 3 — third consecutive breach → confirmed
        result = await monitor.evaluate(_FakeOms(8_000), bot_manager)
        self.assertTrue(result.kill_switch_tripped)
        bot_manager.stop_all_bots.assert_awaited_once()
        self.assertTrue(store.is_kill_switch_tripped())

    @patch("app.services.bots.risk_monitor.RISK_MAX_DRAWDOWN_PCT", 15.0)
    @patch("app.services.bots.risk_monitor.RISK_KILL_SWITCH_ENABLED", True)
    @patch("app.services.bots.risk_monitor.RISK_WEEKEND_FLATTEN_ENABLED", False)
    @patch("app.services.bots.risk_monitor.RISK_POSITION_DURATION_ENABLED", False)
    @patch("app.services.bots.risk_monitor.RISK_DYNAMIC_CORRELATION_ENABLED", False)
    @patch("app.services.bots.risk_monitor.build_portfolio_snapshot")
    async def test_breach_recovery_resets_counter(self, mock_snapshot):
        """If drawdown recovers between ticks, the counter must reset."""
        from app.services.bots.portfolio_risk import PortfolioSnapshot

        bot_manager = self._make_bot_manager()
        monitor = RiskMonitor()
        store.set_equity_peak(10_000)

        # Tick 1 — breach
        mock_snapshot.return_value = PortfolioSnapshot(
            account_equity=8_000, gross_exposure=0,
            group_exposure={}, symbol_exposure={},
        )
        await monitor.evaluate(_FakeOms(8_000), bot_manager)

        # Tick 2 — recovery (above limit)
        mock_snapshot.return_value = PortfolioSnapshot(
            account_equity=9_000, gross_exposure=0,
            group_exposure={}, symbol_exposure={},
        )
        await monitor.evaluate(_FakeOms(9_000), bot_manager)
        self.assertEqual(risk_monitor_mod._breach_counter, 0)

        # Tick 3 — breach again (counter restarts at 1)
        mock_snapshot.return_value = PortfolioSnapshot(
            account_equity=8_000, gross_exposure=0,
            group_exposure={}, symbol_exposure={},
        )
        await monitor.evaluate(_FakeOms(8_000), bot_manager)
        self.assertEqual(risk_monitor_mod._breach_counter, 1)
        self.assertFalse(store.is_kill_switch_tripped())

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

    @patch("app.services.bots.risk_monitor.RISK_MAX_DRAWDOWN_PCT", 15.0)
    @patch("app.services.bots.risk_monitor.build_portfolio_snapshot")
    def test_opening_trade_does_not_trigger_drawdown(self, mock_snapshot):
        """Opening a single position must NOT cause a drawdown reading.

        Scenario: $10,000 cash, buy $5,000 of stock.
        total_equity = cash($5,000) + gross($5,000) = $10,000
        Drawdown should be 0% because total equity hasn't changed.
        """
        from app.services.bots.portfolio_risk import PortfolioSnapshot

        store.set_equity_peak(10_000)

        # After buying: cash is $5,000, position is $5,000 at market
        mock_snapshot.return_value = PortfolioSnapshot(
            account_equity=10_000,  # cash(5k) + position_value(5k)
            gross_exposure=5_000,
            group_exposure={},
            symbol_exposure={},
        )
        snap = compute_drawdown(_FakeOms(5_000, gross=5_000))
        # Total equity is still $10k → drawdown = 0%
        self.assertEqual(snap.current_drawdown_pct, 0.0)
        # Peak should NOT ratchet while position is open (gross > 0)
        self.assertEqual(snap.equity_peak, 10_000)

    @patch("app.services.bots.risk_monitor.RISK_MAX_DRAWDOWN_PCT", 15.0)
    @patch("app.services.bots.risk_monitor.build_portfolio_snapshot")
    def test_unrealized_gains_dont_inflate_peak(self, mock_snapshot):
        """Winning trades should not trip the kill switch.

        Scenario:
        1. Start with $10,000 cash, no positions.
        2. Bot buys — cash=$5,000, gross=$5,000, equity=$10,000.
        3. Price spikes — cash=$5,000, gross=$8,500, equity=$13,500 (unrealized).
        4. Bot sells at profit — cash=$11,500, gross=0, equity=$11,500.

        Peak should stay at $10,000 during step 3 (because gross > 0),
        then ratchet to $11,500 at step 4 (when flat). No drawdown at step 4.
        """
        from app.services.bots.portfolio_risk import PortfolioSnapshot

        # Step 1: initial state, $10,000 cash, flat
        store.set_equity_peak(10_000)

        # Step 3: mid-trade, price spiked — gross exposure inflated
        mock_snapshot.return_value = PortfolioSnapshot(
            account_equity=13_500,  # cash(5k) + gross(8.5k)
            gross_exposure=8_500,
            group_exposure={},
            symbol_exposure={},
        )
        snap_mid = compute_drawdown(_FakeOms(5_000))
        # Peak should NOT ratchet up because we're in a position (gross > 0)
        self.assertEqual(snap_mid.equity_peak, 10_000)
        # Drawdown should actually be 0% because equity ($13.5k) > peak ($10k)
        # but peak isn't updated since gross > 0 — dd_pct = (10k-13.5k)/10k < 0 → 0%
        self.assertEqual(snap_mid.current_drawdown_pct, 0.0)

        # Step 4: trade closed profitably — cash = $11,500, flat
        mock_snapshot.return_value = PortfolioSnapshot(
            account_equity=11_500,  # cash(11.5k) + gross(0)
            gross_exposure=0,
            group_exposure={},
            symbol_exposure={},
        )
        snap_after = compute_drawdown(_FakeOms(11_500))
        # Now flat — peak should ratchet to $11,500
        self.assertEqual(snap_after.equity_peak, 11_500)
        # Drawdown should be 0% — we're at a new high
        self.assertEqual(snap_after.current_drawdown_pct, 0.0)
        self.assertFalse(snap_after.kill_switch_tripped)


if __name__ == "__main__":
    unittest.main()
