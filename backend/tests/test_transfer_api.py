"""API tests for cross-asset model transfer.

Covers:
- POST /api/v1/ml/train rejects an incompatible donor with reasons (400),
- POST /api/v1/ml/train rejects donor == target (400),
- GET /api/v1/ml/transfer/donors returns the compatible-donor list shape,
- the donors endpoint reports enabled=false when transfer is turned off.
"""

import json
import os
import tempfile
import unittest
from unittest import mock
from unittest.mock import MagicMock

_tmp = tempfile.mkdtemp(prefix="transfer_api_test_")
os.environ["TRADING_DB_PATH"] = os.path.join(_tmp, "test.db")

from starlette.testclient import TestClient  # noqa: E402

from app.api.http.app import create_http_app  # noqa: E402
from app.api.router import ensure_routes_loaded  # noqa: E402
from app.api.state import AppState  # noqa: E402
from app.services.bots.ml_feature_engineering import SIGNAL_FEATURE_VERSION  # noqa: E402

_DATA = os.path.join(_tmp, "data")


def _make_state():
    oms = MagicMock()
    oms.get_account_data.return_value = {"balances": [], "positions": {}, "orders": []}
    oms.get_trade_history.return_value = []
    manager = MagicMock()
    manager.connected_clients = set()
    bot_manager = MagicMock()
    bot_manager.list_bots_public.return_value = []
    return AppState(oms=oms, manager=manager, bot_manager=bot_manager, backtester=None)


def _write_gbm_donor(symbol="BTCUSDT", *, schema_version=SIGNAL_FEATURE_VERSION):
    root = os.path.join(_DATA, "ml_signal_models", symbol)
    os.makedirs(root, exist_ok=True)
    meta = {
        "symbol": symbol,
        "timeframe": "1m",
        "model_type": "ml_signal_boost",
        "feature_schema_version": schema_version,
        "trained_at": "2026-08-09T12:00:00Z",
        "version_id": "20260809T120000Z",
        "config": {"atr_mult": 1.25, "max_holding_bars": 12},
        "metrics": {"val_accuracy": 0.55},
    }
    with open(os.path.join(root, "metadata.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)


class TestTransferApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_routes_loaded()

    def setUp(self):
        import shutil

        if os.path.isdir(_DATA):
            shutil.rmtree(_DATA, ignore_errors=True)
        os.makedirs(_DATA, exist_ok=True)
        patches = [
            mock.patch("app.services.bots.ml_model_artifacts.BASE_DIR", _tmp),
            mock.patch("app.config.DATA_DIR", _DATA),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        self.client = TestClient(create_http_app(_make_state()))

    def test_train_with_incompatible_donor_returns_400_with_reasons(self):
        _write_gbm_donor(schema_version=999)  # feature schema mismatch
        resp = self.client.post(
            "/api/v1/ml/train",
            json={
                "symbol": "ADAUSD",
                "strategy": "ML_SIGNAL_BOOST",
                "config": {
                    "timeframe": "1m",
                    "donor": {"symbol": "BTCUSDT"},
                },
            },
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        payload = resp.json()
        self.assertFalse(payload["ok"])
        self.assertIn("not compatible", payload["error"])
        self.assertTrue(any("schema" in r for r in payload.get("reasons") or []))

    def test_train_with_missing_donor_returns_400(self):
        resp = self.client.post(
            "/api/v1/ml/train",
            json={
                "symbol": "ADAUSD",
                "strategy": "ML_SIGNAL_BOOST",
                "config": {
                    "timeframe": "1m",
                    "donor": {"symbol": "NOPEUSD"},
                },
            },
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("no trained", resp.json()["error"])

    def test_train_with_self_donor_returns_400(self):
        resp = self.client.post(
            "/api/v1/ml/train",
            json={
                "symbol": "ADAUSD",
                "strategy": "ML_SIGNAL_BOOST",
                "config": {
                    "timeframe": "1m",
                    "donor": {"symbol": "ADAUSD"},
                },
            },
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("must differ", resp.json()["error"])

    def test_donors_endpoint_shape(self):
        _write_gbm_donor()
        resp = self.client.get(
            "/api/v1/ml/transfer/donors",
            params={
                "strategy": "ML_SIGNAL_BOOST",
                "symbol": "ADAUSD",
                "timeframe": "1m",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["enabled"])
        self.assertEqual(len(payload["donors"]), 1)
        row = payload["donors"][0]
        self.assertEqual(row["symbol"], "BTCUSDT")
        self.assertEqual(row["version_id"], "20260809T120000Z")
        self.assertEqual(row["trained_at"], "2026-08-09T12:00:00Z")
        self.assertIn("has_checkpoint", row)

    def test_donors_endpoint_excludes_target_symbol(self):
        _write_gbm_donor("BTCUSDT")
        _write_gbm_donor("ADAUSD")
        resp = self.client.get(
            "/api/v1/ml/transfer/donors",
            params={"strategy": "ML_SIGNAL_BOOST", "symbol": "ADAUSD"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        symbols = [d["symbol"] for d in resp.json()["donors"]]
        self.assertEqual(symbols, ["BTCUSDT"])

    def test_donors_endpoint_reports_disabled(self):
        with mock.patch("app.config.MODEL_TRANSFER_ENABLED", False):
            resp = self.client.get(
                "/api/v1/ml/transfer/donors",
                params={"strategy": "ML_SIGNAL_BOOST", "symbol": "ADAUSD"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        payload = resp.json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["donors"], [])

    def test_donors_endpoint_requires_params(self):
        resp = self.client.get("/api/v1/ml/transfer/donors")
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
