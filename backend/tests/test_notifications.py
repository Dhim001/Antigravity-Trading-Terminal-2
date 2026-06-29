"""Notification system tests."""

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

_TEST_DIR = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = ""
os.environ["NOTIFICATIONS_ENABLED"] = "true"
os.environ["NOTIFICATION_ENCRYPTION_KEY"] = "test-notification-encryption-key-32chars!"

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "notify_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH
app_config.NOTIFICATION_ENCRYPTION_KEY = os.environ["NOTIFICATION_ENCRYPTION_KEY"]

from app.database import init_db  # noqa: E402
from app.services.notifications.crypto import decrypt_config, encrypt_config  # noqa: E402
from app.services.notifications.dedupe import make_dedupe_key  # noqa: E402
from app.services.notifications.events import NotificationEvent  # noqa: E402
from app.services.notifications import store as notify_store  # noqa: E402
from app.services.notifications.adapters.webhook import build_webhook_payload  # noqa: E402


class NotificationStoreFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_push_channel_gets_subscribe_secret(self):
        row = notify_store.upsert_channel(
            channel_id=None,
            channel_type="push",
            name="Browser",
            enabled=True,
            event_types=["trade_fill"],
            config={},
        )
        self.assertTrue(row.get("has_subscribe_secret"))
        decrypted = notify_store.get_channel_decrypted(row["id"])
        self.assertTrue((decrypted.get("config") or {}).get("subscribe_secret"))
        notify_store.delete_channel(row["id"])

    def test_list_enabled_skips_undecryptable_channels(self):
        row = notify_store.upsert_channel(
            channel_id=None,
            channel_type="webhook",
            name="Broken",
            enabled=True,
            event_types=["trade_fill"],
            config={"url": "https://example.com/hook", "preset": "generic"},
        )
        with patch(
            "app.services.notifications.store.decrypt_config",
            side_effect=ValueError("bad key"),
        ):
            enabled = notify_store.list_enabled_channels()
        self.assertEqual(enabled, [])
        notify_store.delete_channel(row["id"])


class DigestStateTests(unittest.TestCase):
    def test_last_digest_date_roundtrip(self):
        from app.services.runtime import system_state

        system_state.set_last_digest_date("2026-06-29")
        self.assertEqual(system_state.get_last_digest_date(), "2026-06-29")


class NotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_encrypt_roundtrip(self):
        cfg = {"url": "https://example.com/hook", "preset": "slack", "hmac_secret": "sekret"}
        blob = encrypt_config(cfg)
        self.assertNotIn("sekret", blob)
        self.assertEqual(decrypt_config(blob), cfg)

    def test_channel_crud_masks_secrets(self):
        row = notify_store.upsert_channel(
            channel_id=None,
            channel_type="webhook",
            name="Test Hook",
            enabled=True,
            event_types=["trade_fill"],
            config={"url": "https://hooks.slack.com/test", "preset": "slack"},
        )
        self.assertIn("url_masked", row)
        self.assertNotIn("https://hooks.slack.com/test", row.get("url_masked", ""))
        channels = notify_store.list_channels()
        self.assertEqual(len(channels), 1)
        self.assertTrue(notify_store.delete_channel(row["id"]))

    def test_dedupe_key_stable(self):
        ev = NotificationEvent(
            event_type="trade_fill",
            title="Fill",
            body="BUY AAPL",
            bot_id="b1",
            symbol="AAPL",
            timestamp=1_700_000_000.0,
        )
        k1 = make_dedupe_key(ev, "ch1")
        k2 = make_dedupe_key(ev, "ch1")
        k3 = make_dedupe_key(ev, "ch2")
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)

    def test_slack_payload(self):
        ev = NotificationEvent(
            event_type="sl_tp_trigger",
            title="SL hit",
            body="Exit with loss",
            severity="warn",
            symbol="BTCUSDT",
        )
        payload = build_webhook_payload(ev, "slack")
        self.assertIn("text", payload)
        self.assertIn("SL hit", payload["text"])

    def test_dedupe_claim_once(self):
        row = notify_store.upsert_channel(
            channel_id=None,
            channel_type="webhook",
            name="Dedupe",
            enabled=True,
            event_types=["*"],
            config={"url": "https://example.com", "preset": "generic"},
        )
        ev = NotificationEvent(event_type="test", title="T", body="B", timestamp=100.0)
        key = make_dedupe_key(ev, row["id"])
        self.assertTrue(
            notify_store.try_claim_delivery(
                log_id="log1",
                dedupe_key=key,
                channel_id=row["id"],
                event_type="test",
                payload={},
            )
        )
        self.assertFalse(
            notify_store.try_claim_delivery(
                log_id="log2",
                dedupe_key=key,
                channel_id=row["id"],
                event_type="test",
                payload={},
            )
        )
        notify_store.delete_channel(row["id"])


class TelegramEmailChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_telegram_channel_masks_secrets(self):
        row = notify_store.upsert_channel(
            channel_id=None,
            channel_type="telegram",
            name="TG",
            enabled=True,
            event_types=["trade_fill"],
            config={"bot_token": "123456:ABC-DEF", "chat_id": "-100999888"},
        )
        self.assertIn("bot_token_masked", row)
        self.assertNotIn("ABC-DEF", row.get("bot_token_masked", ""))
        notify_store.delete_channel(row["id"])

    def test_email_channel_defaults_digest(self):
        row = notify_store.upsert_channel(
            channel_id=None,
            channel_type="email",
            name="Mail",
            enabled=True,
            event_types=["daily_digest"],
            config={
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "smtp_user": "bot@example.com",
                "smtp_password": "secret",
                "from_address": "bot@example.com",
                "to_addresses": ["ops@example.com"],
            },
        )
        self.assertEqual(row["channel_type"], "email")
        self.assertIn("smtp_host", row)
        notify_store.delete_channel(row["id"])

    def test_digest_dedupe_by_date(self):
        ev = NotificationEvent(
            event_type="daily_digest",
            title="Digest",
            body="body",
            payload={"digest_date": "2026-06-29"},
        )
        k1 = make_dedupe_key(ev, "ch-digest")
        ev2 = NotificationEvent(
            event_type="daily_digest",
            title="Digest",
            body="body",
            payload={"digest_date": "2026-06-30"},
        )
        self.assertNotEqual(k1, make_dedupe_key(ev2, "ch-digest"))


class NotificationDispatchTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    async def test_emit_dispatches_webhook(self):
        row = notify_store.upsert_channel(
            channel_id=None,
            channel_type="webhook",
            name="Async",
            enabled=True,
            event_types=["bot_log_error"],
            config={"url": "https://example.com/hook", "preset": "generic"},
        )
        from app.services.notifications.dispatcher import emit_notification

        with patch(
            "app.services.notifications.adapters.webhook.deliver_webhook",
            new_callable=AsyncMock,
        ) as mock_deliver:
            count = await emit_notification(
                NotificationEvent(
                    event_type="bot_log_error",
                    title="Error",
                    body="Something failed",
                    severity="error",
                )
            )
            self.assertEqual(count, 1)
            await asyncio.sleep(0.05)
            mock_deliver.assert_called_once()
        notify_store.delete_channel(row["id"])

    async def test_emit_dispatches_telegram(self):
        row = notify_store.upsert_channel(
            channel_id=None,
            channel_type="telegram",
            name="TG Async",
            enabled=True,
            event_types=["bot_log_error"],
            config={"bot_token": "1:token", "chat_id": "123"},
        )
        from app.services.notifications.dispatcher import emit_notification

        with patch(
            "app.services.notifications.adapters.telegram.deliver_telegram",
            new_callable=AsyncMock,
        ) as mock_deliver:
            count = await emit_notification(
                NotificationEvent(
                    event_type="bot_log_error",
                    title="Error",
                    body="Telegram test",
                    severity="error",
                )
            )
            self.assertEqual(count, 1)
            await asyncio.sleep(0.05)
            mock_deliver.assert_called_once()
        notify_store.delete_channel(row["id"])


import asyncio  # noqa: E402

if __name__ == "__main__":
    unittest.main()
