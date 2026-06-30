"""Alert rule tests."""

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

_TEST_DIR = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = ""
os.environ["NOTIFICATIONS_ENABLED"] = "true"
os.environ["NOTIFICATION_ENCRYPTION_KEY"] = "test-notification-encryption-key-32chars!"
os.environ["ALERT_RULES_ENABLED"] = "true"

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "alert_rules_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH
app_config.NOTIFICATION_ENCRYPTION_KEY = os.environ["NOTIFICATION_ENCRYPTION_KEY"]

from app.database import init_db  # noqa: E402
from app.services.notifications.alert_rules import evaluator, store  # noqa: E402
from app.services.notifications.alert_rules import types as atypes  # noqa: E402
from app.services.notifications.dedupe import make_dedupe_key  # noqa: E402
from app.services.notifications.events import NotificationEvent  # noqa: E402
from tests.test_chart_agent_rules import make_trending_candles  # noqa: E402


class AlertRuleStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_crud_rule(self):
        row = store.upsert_rule(
            rule_id=None,
            name="RSI high",
            enabled=True,
            symbol="BTCUSDT",
            timeframe="1h",
            condition_type=atypes.RSI_ABOVE,
            threshold=70.0,
            signal=None,
            cooldown_sec=600,
            notify_channels=None,
        )
        self.assertEqual(row["symbol"], "BTCUSDT")
        self.assertIsNone(row["notify_channels"])
        self.assertEqual(row["timeframe"], "1h")
        rules = store.list_rules(symbol="BTCUSDT")
        self.assertEqual(len(rules), 1)
        self.assertTrue(store.delete_rule(row["id"]))

    def test_cooldown(self):
        row = store.upsert_rule(
            rule_id=None,
            name="Price",
            enabled=True,
            symbol="AAPL",
            timeframe="1m",
            condition_type=atypes.PRICE_ABOVE,
            threshold=100.0,
            signal=None,
            cooldown_sec=300,
            notify_channels=None,
        )
        self.assertFalse(store.is_in_cooldown(row))
        store.mark_triggered(row["id"])
        updated = store.get_rule(row["id"])
        self.assertTrue(store.is_in_cooldown(updated))
        store.delete_rule(row["id"])


class AlertRuleEvaluatorTests(unittest.TestCase):
    def test_price_above_matches(self):
        candles = make_trending_candles(80)
        metrics = evaluator.compute_bar_metrics("TEST", candles)
        self.assertIsNotNone(metrics)
        rule = {"condition_type": atypes.PRICE_ABOVE, "threshold": metrics["close"] - 1}
        self.assertTrue(evaluator.rule_matches(rule, metrics))

    def test_rsi_above_requires_rsi(self):
        rule = {"condition_type": atypes.RSI_ABOVE, "threshold": 50}
        self.assertFalse(evaluator.rule_matches(rule, {"rsi": None, "close": 1}))
        self.assertTrue(evaluator.rule_matches(rule, {"rsi": 55, "close": 1}))


class AlertRuleDedupeTests(unittest.TestCase):
    def test_dedupe_by_rule_and_bar(self):
        ev = NotificationEvent(
            event_type="alert_rule",
            title="T",
            body="B",
            payload={"rule_id": "r1", "bar_time": 1000},
        )
        k1 = make_dedupe_key(ev, "ch1")
        ev2 = NotificationEvent(
            event_type="alert_rule",
            title="T",
            body="B",
            payload={"rule_id": "r1", "bar_time": 2000},
        )
        self.assertNotEqual(k1, make_dedupe_key(ev2, "ch1"))


class AlertRuleEngineTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    async def test_maybe_evaluate_skips_ht_on_live_massive(self):
        from app.services.notifications.alert_rules import engine

        store.upsert_rule(
            rule_id=None,
            name="HT rule",
            enabled=True,
            symbol="AAPL",
            timeframe="1h",
            condition_type=atypes.PRICE_ABOVE,
            threshold=1.0,
            signal=None,
            cooldown_sec=60,
            notify_channels=None,
        )

        with patch.object(engine, "evaluate_rules_for_bar", new_callable=AsyncMock) as mock_eval:
            with patch("app.services.notifications.alert_rules.engine.is_live_massive", return_value=True):
                with patch(
                    "app.services.notifications.alert_rules.engine.get_bot_candles",
                    return_value=make_trending_candles(80),
                ):
                    await engine.maybe_evaluate_alert_rules("AAPL", ohlcv_1m=make_trending_candles(80), feed=object())
            mock_eval.assert_not_called()

        rules = store.list_rules(symbol="AAPL")
        for r in rules:
            store.delete_rule(r["id"])


class AlertRuleDispatchTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    async def test_evaluate_rules_emits_notification(self):
        from app.services.notifications import store as notify_store
        from app.services.notifications.alert_rules.engine import evaluate_rules_for_bar

        ch = notify_store.upsert_channel(
            channel_id=None,
            channel_type="webhook",
            name="Alerts",
            enabled=True,
            event_types=["alert_rule"],
            config={"url": "https://example.com/hook", "preset": "generic"},
        )
        rule = store.upsert_rule(
            rule_id=None,
            name="Low RSI",
            enabled=True,
            symbol="TEST",
            timeframe="1m",
            condition_type=atypes.RSI_BELOW,
            threshold=100.0,
            signal=None,
            cooldown_sec=60,
            notify_channels=None,
        )
        candles = make_trending_candles(80)

        with patch(
            "app.services.notifications.adapters.webhook.deliver_webhook",
            new_callable=AsyncMock,
        ) as mock_deliver:
            count = await evaluate_rules_for_bar("TEST", "1m", candles)
            self.assertGreaterEqual(count, 1)
            import asyncio
            await asyncio.sleep(0.05)
            mock_deliver.assert_called()

        store.delete_rule(rule["id"])
        notify_store.delete_channel(ch["id"])

    async def test_empty_notify_channels_skips_emit(self):
        from app.services.notifications import store as notify_store
        from app.services.notifications.alert_rules.engine import evaluate_rules_for_bar

        notify_store.upsert_channel(
            channel_id=None,
            channel_type="webhook",
            name="Alerts",
            enabled=True,
            event_types=["alert_rule"],
            config={"url": "https://example.com/hook", "preset": "generic"},
        )
        rule = store.upsert_rule(
            rule_id=None,
            name="Muted",
            enabled=True,
            symbol="TEST",
            timeframe="1m",
            condition_type=atypes.RSI_BELOW,
            threshold=100.0,
            signal=None,
            cooldown_sec=60,
            notify_channels=[],
        )
        candles = make_trending_candles(80)

        with patch(
            "app.services.notifications.adapters.webhook.deliver_webhook",
            new_callable=AsyncMock,
        ) as mock_deliver:
            count = await evaluate_rules_for_bar("TEST", "1m", candles)
            self.assertEqual(count, 0)
            mock_deliver.assert_not_called()

        store.delete_rule(rule["id"])
        for ch in notify_store.list_channels():
            notify_store.delete_channel(ch["id"])


class AlertRuleChannelTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_notify_channels_encoding(self):
        row = store.upsert_rule(
            rule_id=None,
            name="Targeted",
            enabled=True,
            symbol="ETHUSDT",
            timeframe="1m",
            condition_type=atypes.PRICE_ABOVE,
            threshold=1.0,
            signal=None,
            cooldown_sec=60,
            notify_channels=["ch-1", "ch-2"],
        )
        self.assertEqual(row["notify_channels"], ["ch-1", "ch-2"])
        none_row = store.upsert_rule(
            rule_id=None,
            name="All",
            enabled=True,
            symbol="ETHUSDT",
            timeframe="1m",
            condition_type=atypes.PRICE_ABOVE,
            threshold=1.0,
            signal=None,
            cooldown_sec=60,
            notify_channels=None,
        )
        self.assertIsNone(none_row["notify_channels"])
        empty_row = store.upsert_rule(
            rule_id=None,
            name="None",
            enabled=True,
            symbol="ETHUSDT",
            timeframe="1m",
            condition_type=atypes.PRICE_ABOVE,
            threshold=1.0,
            signal=None,
            cooldown_sec=60,
            notify_channels=[],
        )
        self.assertEqual(empty_row["notify_channels"], [])
        store.delete_rule(row["id"])
        store.delete_rule(none_row["id"])
        store.delete_rule(empty_row["id"])


if __name__ == "__main__":
    unittest.main()
