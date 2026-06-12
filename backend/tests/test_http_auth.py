"""HTTP API key middleware tests."""

import unittest
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from app.api.http.app import create_http_app
from app.api.router import ensure_routes_loaded
from app.api.state import AppState
from app.config import HTTP_API_KEY


def _make_state():
    oms = MagicMock()
    oms.get_account_data.return_value = {"balances": [], "positions": {}, "orders": []}
    oms.get_trade_history.return_value = []
    manager = MagicMock()
    manager.connected_clients = set()
    bot_manager = MagicMock()
    bot_manager.list_bots_public.return_value = []
    return AppState(oms=oms, manager=manager, bot_manager=bot_manager, backtester=None)


class TestHttpApiKeyAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_routes_loaded()

    def setUp(self):
        self._orig_key = HTTP_API_KEY

    def tearDown(self):
        import app.config as config
        import app.api.http.app as http_app
        config.HTTP_API_KEY = self._orig_key
        http_app.HTTP_API_KEY = self._orig_key

    def _client_with_key(self, key: str) -> TestClient:
        import app.config as config
        import app.api.http.app as http_app
        config.HTTP_API_KEY = key
        http_app.HTTP_API_KEY = key
        return TestClient(create_http_app(_make_state()))

    def test_health_public_without_key(self):
        client = self._client_with_key("secret-key")
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_protected_route_requires_key(self):
        client = self._client_with_key("secret-key")
        resp = client.get("/api/v1/account")
        self.assertEqual(resp.status_code, 401)

        resp = client.get("/api/v1/account", headers={"X-API-Key": "secret-key"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

    def test_no_auth_when_key_unset(self):
        client = self._client_with_key("")
        resp = client.get("/api/v1/account")
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
