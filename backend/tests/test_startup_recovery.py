"""Tests for startup recovery and safe mode."""

import os
import unittest

from app.database import get_connection, init_db
from app.services.bots import signal_ledger
from app.services.runtime import system_state
from app.services.runtime.startup_recovery import confirm_safe_mode


class TestStartupRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        signal_ledger.clear_signal_ledger()
        system_state.clear_safe_mode()
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM system_runtime")
        cur.execute(
            """
            INSERT OR IGNORE INTO bots (id, strategy, symbol, timeframe, status, allocation, config)
            VALUES ('bot-r', 'MACD_RSI', 'AAPL', '1m', 'RUNNING', 1000, '{}')
            """
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        if "TERMINAL_ROLE" in os.environ:
            del os.environ["TERMINAL_ROLE"]

    def test_unclean_shutdown_is_role_scoped(self):
        os.environ["TERMINAL_ROLE"] = "server"
        system_state.mark_shutdown_clean()
        system_state.mark_process_starting()
        self.assertFalse(system_state.was_unclean_shutdown())

        os.environ["TERMINAL_ROLE"] = "worker"
        self.assertFalse(system_state.was_unclean_shutdown())

        system_state.mark_process_starting()
        self.assertTrue(system_state.was_unclean_shutdown())

    def test_safe_mode_lifecycle(self):
        system_state.enter_safe_mode("test", details={"foo": 1})
        self.assertTrue(system_state.is_safe_mode_active())
        info = system_state.get_safe_mode_info()
        self.assertEqual(info.get("reason"), "test")

        result = confirm_safe_mode()
        self.assertTrue(result.get("cleared"))
        self.assertFalse(system_state.is_safe_mode_active())


if __name__ == "__main__":
    unittest.main()
