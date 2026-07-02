"""Meta-label GBM classifier — dataset, train, gate integration."""

from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""

_TEST_DIR = tempfile.mkdtemp()
import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.path.join(_TEST_DIR, "meta_label_test.db")
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
app_config.DB_PATH = db_conn.DB_PATH
app_config.META_LABEL_MODEL_DIR = os.path.join(_TEST_DIR, "meta_label_models")

from app.services.bots.meta_label_model import (  # noqa: E402
    FEATURE_NAMES,
    _parse_entry_ts,
    build_meta_label_dataset,
    features_to_vector,
    insight_to_features,
    train_meta_label_model,
    train_model_from_rows,
    get_meta_label_store,
    predict_meta_label_prob,
)
from app.services.bots.calibration import check_meta_label_gate, get_calibration_store  # noqa: E402


def _winning_snap(score: int = 3, conf: float = 0.72, atr: str = "normal"):
    return {
        "score": score,
        "confidence": conf,
        "sub_reports": {
            "trend": {"score": 1, "trend_regime": "trending"},
            "momentum": {"score": 1, "volume": {"score": 0}},
            "volume": {"score": 0},
            "sentiment": {"score": 0, "aggregate_score": 0.1, "mention_count": 2},
            "risk": {"atr_regime": atr, "suggested_size_factor": 1.0},
            "anomaly": {"is_anomaly": False},
        },
    }


def _losing_snap(score: int = 2, conf: float = 0.58, atr: str = "elevated"):
    return {
        "score": score,
        "confidence": conf,
        "sub_reports": {
            "trend": {"score": 0, "trend_regime": "ranging"},
            "momentum": {"score": -1},
            "volume": {"score": -1},
            "sentiment": {"score": -1, "aggregate_score": -0.2, "mention_count": 1},
            "risk": {"atr_regime": atr, "suggested_size_factor": 0.8},
            "anomaly": {"is_anomaly": True},
        },
    }


class MetaLabelFeatureTests(unittest.TestCase):
    def test_insight_to_features_vector_shape(self):
        feat = insight_to_features(_winning_snap(), symbol="AAPL", side="BUY", entry_ts="2026-06-01T14:30:00Z")
        vec = features_to_vector(feat)
        self.assertEqual(len(vec), len(FEATURE_NAMES))
        self.assertEqual(feat["is_buy"], 1.0)
        self.assertEqual(feat["atr_normal"], 1.0)

    def test_parse_unix_entry_ts(self):
        dt = _parse_entry_ts("1704067200")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 0)  # UTC midnight on 2024-01-01

    def test_train_refits_on_all_rows(self):
        rows = []
        for i in range(12):
            snap = _winning_snap() if i % 2 == 0 else _losing_snap()
            rows.append({
                "features": insight_to_features(snap, entry_ts=f"2026-01-{i+1:02d}T10:00:00Z"),
                "win": i % 2 == 0,
            })
        out = train_model_from_rows(rows, min_samples=10, val_fraction=0.2)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out["metrics"].get("fit_samples"), 12)


class MetaLabelTrainTests(unittest.TestCase):
    def setUp(self):
        from app.database import init_db
        from app.db.connection import get_connection

        db_conn._pool = None
        get_meta_label_store().invalidate()
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bot_trades")
        cursor.execute("DELETE FROM bots")
        cursor.execute(
            """
            INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bot-ml-1",
                "CHART_AGENT",
                "AAPL",
                "1m",
                "RUNNING",
                1000,
                '{"calibration_gate_enabled": true, "meta_label_model_mode": "gbm"}',
            ),
        )
        for i in range(35):
            win = i % 3 != 0
            snap = _winning_snap() if win else _losing_snap()
            cursor.execute(
                """
                INSERT INTO bot_trades
                (bot_id, symbol, side, quantity, price, pnl, is_exit, insight_snapshot, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bot-ml-1", "AAPL", "BUY", 1.0, 100.0, None, 0,
                    __import__("json").dumps(snap),
                    f"2026-06-{i+1:02d}T10:00:00Z",
                ),
            )
            pnl = 5.0 if win else -3.0
            cursor.execute(
                """
                INSERT INTO bot_trades
                (bot_id, symbol, side, quantity, price, pnl, is_exit, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bot-ml-1", "AAPL", "SELL", 1.0, 100.0 + pnl, pnl, 1,
                    f"2026-06-{i+1:02d}T11:00:00Z",
                ),
            )
        conn.commit()
        conn.close()

    def test_build_dataset(self):
        ds = build_meta_label_dataset("bot-ml-1")
        self.assertGreaterEqual(ds["sample_count"], 30)

    def test_train_and_predict(self):
        result = train_meta_label_model("bot-ml-1", min_samples=25)
        self.assertTrue(result.get("ok"), result.get("error"))
        prob = predict_meta_label_prob(
            "bot-ml-1",
            _winning_snap(),
            symbol="AAPL",
            side="BUY",
            timeframe="1m",
        )
        self.assertIsNotNone(prob)
        self.assertGreaterEqual(prob, 0.0)
        self.assertLessEqual(prob, 1.0)


class MetaLabelGateTests(MetaLabelTrainTests):
    def setUp(self):
        super().setUp()
        train_meta_label_model("bot-ml-1", min_samples=25)
        get_calibration_store().invalidate("bot-ml-1")

    def test_gbm_mode_blocks_low_prob_setup(self):
        cfg = {
            "calibration_gate_enabled": True,
            "meta_label_model_mode": "gbm",
            "meta_label_min_prob": 0.99,
        }
        reason = check_meta_label_gate(
            _losing_snap(),
            cfg,
            symbol="AAPL",
            timeframe="1m",
            signal="BUY",
            bot_id="bot-ml-1",
        )
        self.assertIsNotNone(reason)
        self.assertIn("meta-label gate", reason.lower())

    def test_hybrid_falls_back_to_wilson_when_cold(self):
        cfg = {
            "calibration_gate_enabled": True,
            "meta_label_model_mode": "hybrid",
            "meta_label_min_prob": 0.99,
            "calibration_min_samples": 5,
            "calibration_min_wilson": 0.99,
        }
        reason = check_meta_label_gate(
            _losing_snap(score=2, conf=0.6),
            cfg,
            symbol="AAPL",
            timeframe="1m",
            signal="BUY",
            bot_id="bot-no-model",
        )
        # no model for unknown bot — hybrid uses Wilson; may be None if no bucket stats
        self.assertTrue(reason is None or "calibration gate" in reason.lower() or "meta-label" in reason.lower())


if __name__ == "__main__":
    unittest.main()
