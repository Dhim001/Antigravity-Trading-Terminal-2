"""Web Push notification tests."""

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

_TEST_DIR = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = ""
os.environ["NOTIFICATIONS_ENABLED"] = "true"
os.environ["NOTIFICATION_ENCRYPTION_KEY"] = "test-notification-encryption-key-32chars!"
os.environ["WEB_PUSH_ENABLED"] = "true"
os.environ["VAPID_PUBLIC_KEY"] = "BEl62iUYgUivxIkv69yViEuiBIa-Ib27-SkxY6jrC8s"
os.environ["VAPID_PRIVATE_KEY"] = "UUxI4O8-FbRWDArN8fX2t9gT7X9X9X9X9X9X9X9X9X9"
os.environ["VAPID_SUBJECT"] = "mailto:test@example.com"

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "push_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH
app_config.NOTIFICATION_ENCRYPTION_KEY = os.environ["NOTIFICATION_ENCRYPTION_KEY"]
app_config.VAPID_PUBLIC_KEY = os.environ["VAPID_PUBLIC_KEY"]
app_config.VAPID_PRIVATE_KEY = os.environ["VAPID_PRIVATE_KEY"]
app_config.VAPID_SUBJECT = os.environ["VAPID_SUBJECT"]

from app.database import init_db  # noqa: E402
from app.services.notifications import store as notify_store  # noqa: E402
from app.services.notifications import push_subscriptions as push_store  # noqa: E402
from app.services.notifications.events import NotificationEvent  # noqa: E402
from app.services.notifications.vapid import get_vapid_public_key, web_push_configured  # noqa: E402


class PushSubscriptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_subscription_roundtrip(self):
        ch = notify_store.upsert_channel(
            channel_id=None,
            channel_type="push",
            name="Browser",
            enabled=True,
            event_types=["trade_fill"],
            config={},
        )
        row = push_store.upsert_subscription(
            channel_id=ch["id"],
            endpoint="https://push.example.com/sub/abc123",
            p256dh="key1",
            auth="auth1",
            user_agent="test",
        )
        self.assertIn("endpoint_masked", row)
        subs = push_store.list_subscriptions_decrypted(ch["id"])
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["keys"]["p256dh"], "key1")
        push_store.delete_subscriptions_for_channel(ch["id"])
        notify_store.delete_channel(ch["id"])

    def test_vapid_configured(self):
        self.assertTrue(web_push_configured())
        self.assertEqual(get_vapid_public_key(), os.environ["VAPID_PUBLIC_KEY"])


class PushDispatchTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    async def test_deliver_push_calls_webpush(self):
        from app.services.notifications.adapters.push import deliver_push

        ch = notify_store.upsert_channel(
            channel_id=None,
            channel_type="push",
            name="Push",
            enabled=True,
            event_types=["test"],
            config={},
        )
        push_store.upsert_subscription(
            channel_id=ch["id"],
            endpoint="https://push.example.com/sub/xyz",
            p256dh="p256",
            auth="auth",
        )
        channel = notify_store.get_channel_decrypted(ch["id"])
        event = NotificationEvent(
            event_type="test",
            title="Push test",
            body="Hello push",
            severity="info",
        )

        with patch(
            "app.services.notifications.adapters.push._send_one",
        ) as mock_send:
            await deliver_push(event, channel)
            mock_send.assert_called_once()

        push_store.delete_subscriptions_for_channel(ch["id"])
        notify_store.delete_channel(ch["id"])


class PushSubscribeValidationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    async def test_subscribe_rejects_non_push_channel(self):
        from app.api.context import RequestContext
        from app.api.handlers.notifications import notify_push_subscribe

        webhook = notify_store.upsert_channel(
            channel_id=None,
            channel_type="webhook",
            name="Hook",
            enabled=True,
            event_types=["trade_fill"],
            config={"url": "https://example.com/hook", "preset": "generic"},
        )
        ctx = RequestContext(
            websocket=object(),
            manager=MagicMock(),
            oms=MagicMock(),
            bot_manager=MagicMock(),
            backtester=None,
            chart_analyst=None,
            message={
                "channel_id": webhook["id"],
                "subscribe_secret": "nope",
                "subscription": {"endpoint": "https://push.example/x", "keys": {"p256dh": "a", "auth": "b"}},
            },
            action="notify_push_subscribe",
        )
        results: list = []

        async def capture(_ctx, payload):
            results.append(payload)

        with patch("app.api.handlers.notifications.send_order_result", side_effect=capture):
            with patch("app.services.notifications.vapid.web_push_configured", return_value=True):
                await notify_push_subscribe(ctx)
        self.assertEqual(results[0]["status"], "error")
        self.assertIn("not a push", results[0]["message"].lower())
        notify_store.delete_channel(webhook["id"])

    async def test_subscribe_requires_valid_secret(self):
        from app.api.context import RequestContext
        from app.api.handlers.notifications import notify_push_subscribe

        push_ch = notify_store.upsert_channel(
            channel_id=None,
            channel_type="push",
            name="Push",
            enabled=True,
            event_types=["trade_fill"],
            config={},
        )
        secret = notify_store.get_channel_decrypted(push_ch["id"])["config"]["subscribe_secret"]
        ctx = RequestContext(
            websocket=object(),
            manager=MagicMock(),
            oms=MagicMock(),
            bot_manager=MagicMock(),
            backtester=None,
            chart_analyst=None,
            message={
                "channel_id": push_ch["id"],
                "subscribe_secret": "wrong",
                "subscription": {"endpoint": "https://push.example/y", "keys": {"p256dh": "a", "auth": "b"}},
            },
            action="notify_push_subscribe",
        )
        results: list = []

        async def capture(_ctx, payload):
            results.append(payload)

        with patch("app.api.handlers.notifications.send_order_result", side_effect=capture):
            with patch("app.services.notifications.vapid.web_push_configured", return_value=True):
                await notify_push_subscribe(ctx)
        self.assertEqual(results[0]["status"], "error")
        self.assertIn("subscribe_secret", results[0]["message"].lower())

        ctx.message["subscribe_secret"] = secret
        with patch("app.api.handlers.notifications.send_order_result", side_effect=capture):
            with patch("app.services.notifications.vapid.web_push_configured", return_value=True):
                await notify_push_subscribe(ctx)
        self.assertEqual(results[-1]["status"], "success")
        notify_store.delete_channel(push_ch["id"])


if __name__ == "__main__":
    unittest.main()
