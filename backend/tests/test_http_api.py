"""Tests for HTTP bindings, OpenAPI, and middleware."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from starlette.testclient import TestClient

from app.api.http.app import create_http_app
from app.api.http.bindings import HTTP_BINDINGS
from app.api.middleware import middleware_rate_limit
from app.api.openapi import build_openapi_spec
from app.api.context import RequestContext
from app.api.protocol import Action
from app.api.types import RouteMeta
from app.api.router import ensure_routes_loaded
from app.api.state import AppState
from app.api.http.collector import HttpConnectionManager


def _make_state():
    oms = MagicMock()
    oms.get_account_data.return_value = {"balances": [{"asset": "USD", "balance": 10000}]}
    oms.get_trade_history.return_value = []
    oms.place_order = AsyncMock(return_value={"status": "success", "order_id": "ord-1"})
    oms.cancel_order = AsyncMock(return_value={"status": "success"})

    bot_manager = MagicMock()
    bot_manager.list_bots_public.return_value = []
    bot_manager.get_bot_detail.return_value = {"id": "bot-1"}
    bot_manager.create_bot = AsyncMock(return_value="bot-2")
    bot_manager.stop_bot = AsyncMock()
    bot_manager.pause_bot = AsyncMock()
    bot_manager.resume_bot = AsyncMock()
    bot_manager.stop_all_bots = AsyncMock(return_value=1)

    manager = MagicMock()
    manager.connected_clients = set()

    return AppState(oms=oms, manager=manager, bot_manager=bot_manager, backtester=None, chart_analyst=None)


class TestHttpBindings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_routes_loaded()
        cls.client = TestClient(create_http_app(_make_state()))

    def test_all_bindings_have_registered_actions(self):
        from app.api.router import ROUTES
        for _method, _path, action, _ in HTTP_BINDINGS:
            self.assertIn(action, ROUTES)

    def test_health(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertIn("allow_live_bots", body)
        self.assertIn("allow_custom_strategies", body)
        self.assertIn("archive_parquet_enabled", body)
        self.assertIn("archive_backend", body)

    def test_session_operator_env_fields(self):
        resp = self.client.get("/api/v1/session")
        self.assertEqual(resp.status_code, 200)
        terminal = resp.json()["session"]["terminal"]
        self.assertIn("allow_custom_strategies", terminal)
        self.assertIn("archive_parquet_enabled", terminal)
        self.assertIn("archive_backend", terminal)

    def test_health_live(self):
        resp = self.client.get("/health/live")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["service"], "trading-terminal")
        self.assertIn("terminal_mode", body)
        self.assertIn("ws_clients", body)

    def test_health_is_cached(self):
        first = self.client.get("/health?fresh=1")
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json().get("cached"))
        second = self.client.get("/health")
        self.assertEqual(second.status_code, 200)
        body = second.json()
        self.assertTrue(body.get("ok"))
        self.assertTrue(body.get("cached"))

    def test_session(self):
        resp = self.client.get("/api/v1/session")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        session = body["session"]
        self.assertIn("terminal", session)
        self.assertIn("account", session)
        self.assertIn("bots", session)
        self.assertIn("strategies", session)

    def test_openapi_spec(self):
        spec = build_openapi_spec()
        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertIn("/api/v1/bots", spec["paths"])
        self.assertIn("/api/v1/scanner/scan", spec["paths"])
        self.assertIn("/api/v1/admin/emergency-stop", spec["paths"])

    def test_openapi_endpoint(self):
        resp = self.client.get("/api/v1/openapi.json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("paths", resp.json())

    def test_cancel_order_route(self):
        resp = self.client.delete("/api/v1/orders/ord-123")
        self.assertEqual(resp.status_code, 200)

    def test_preview_order_route(self):
        state = _make_state()
        state.oms.feed = SimpleNamespace(_symbols={"BTCUSDT": {"price": 50_000}})
        state.oms.get_account_data.return_value = {
            "balances": {"USDT": {"balance": 10_000, "locked": 0}},
            "positions": {},
            "tickers": {"BTCUSDT": {"price": 50_000}},
        }
        client = TestClient(create_http_app(state))
        resp = client.post("/api/v1/orders/preview", json={
            "symbol": "BTCUSDT",
            "type": "MARKET",
            "side": "BUY",
            "quantity": 0.01,
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["type"], "order_preview")
        self.assertTrue(body["data"]["allowed"])

    def test_metrics_route(self):
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/plain", resp.headers.get("content-type", ""))

    def test_scanner_route(self):
        resp = self.client.post("/api/v1/scanner/scan", json={
            "symbols": ["BTCUSDT"],
            "signal_filter": "any",
            "sort_by": "score",
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["type"], "scan_results")
        self.assertIn("rows", body["data"])

    def test_market_candles_route(self):
        state = _make_state()
        state.oms.feed = SimpleNamespace(
            get_candles=lambda symbol: [
                {"time": 1, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
            ],
            get_market_data=lambda symbol: {},
        )
        client = TestClient(create_http_app(state))
        resp = client.get("/api/v1/market/AAPL/candles")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["type"], "history_update")
        self.assertIn("AAPL", body["data"])
        self.assertEqual(len(body["data"]["AAPL"]), 1)

    def test_market_candles_ht_interval_includes_meta(self):
        state = _make_state()
        state.oms.feed = SimpleNamespace(
            get_candles=lambda symbol: [],
            fetch_ht_candles=lambda symbol, interval, limit=None, purpose="chart": [
                {"time": 3600, "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 10},
            ],
            get_market_data=lambda symbol: {},
        )
        client = TestClient(create_http_app(state))
        with patch("app.api.handlers.market.TERMINAL_MODE", "LIVE_MASSIVE"):
            resp = client.get("/api/v1/market/AAPL/candles?interval=1h&limit=600")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body.get("meta", {}).get("interval"), "1h")
        self.assertEqual(body["data"]["AAPL"][0]["time"], 3600)

    def test_market_candles_route_tail_limit(self):
        state = _make_state()
        full = [
            {
                "time": i,
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
                "volume": 1000,
            }
            for i in range(800)
        ]
        state.oms.feed = SimpleNamespace(
            get_candles=lambda symbol: full,
            get_market_data=lambda symbol: {},
        )
        client = TestClient(create_http_app(state))
        resp = client.get("/api/v1/market/AAPL/candles?limit=600")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        bars = body["data"]["AAPL"]
        self.assertEqual(len(bars), 600)
        self.assertEqual(bars[0]["time"], 200)
        self.assertEqual(bars[-1]["time"], 799)

    def test_bot_stop_route(self):
        resp = self.client.post("/api/v1/bots/bot-1/stop")
        self.assertEqual(resp.status_code, 200)

    def test_backtest_route(self):
        resp = self.client.post("/api/v1/backtest", json={
            "symbol": "AAPL",
            "strategy": "MACD_RSI",
            "config": {},
        })
        self.assertIn(resp.status_code, (200, 400))


class TestRateLimitMiddleware(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_blocks_rapid_trades(self):
        manager = HttpConnectionManager()
        oms = MagicMock()
        bot_manager = MagicMock()
        ctx = RequestContext(
            websocket=None,
            manager=manager,
            oms=oms,
            bot_manager=bot_manager,
            backtester=None,
            chart_analyst=None,
            message={"_rate_key": "test-client"},
            action=Action.PLACE_ORDER,
        )
        meta = RouteMeta(handler=AsyncMock(), tags=["trading"])

        first = await middleware_rate_limit(ctx, meta)
        second = await middleware_rate_limit(ctx, meta)
        self.assertFalse(first)
        self.assertTrue(second)
        self.assertTrue(any(m.get("type") == "order_result" for m in manager.messages))


if __name__ == "__main__":
    unittest.main()
