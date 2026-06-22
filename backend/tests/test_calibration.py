"""Tests for trade calibration and filter-reject aggregation."""

import json
import unittest

from app.database import get_connection, init_db
from app.services.bots.calibration import (
    CalibrationStore,
    aggregate_live_filter_rejects,
    build_config_patch_from_suggestions,
    check_meta_label_gate,
    compute_calibration_apply_patch,
    confidence_bucket,
    get_calibration,
    get_calibration_store,
    pair_closed_trades,
    score_bucket,
    setup_bucket_key,
    suggest_thresholds,
    wilson_lower_bound,
)


class TestCalibrationMath(unittest.TestCase):
    def test_wilson_lower_small_sample(self):
        # 3/3 wins should not report 100% lower bound
        lower = wilson_lower_bound(3, 3)
        self.assertLess(lower, 1.0)
        self.assertGreater(lower, 0.0)

    def test_wilson_zero_trades(self):
        self.assertEqual(wilson_lower_bound(0, 0), 0.0)

    def test_score_bucket(self):
        self.assertEqual(score_bucket(2), "2")
        self.assertEqual(score_bucket(4), "4+")
        self.assertEqual(score_bucket(-3), "3")

    def test_confidence_bucket(self):
        self.assertEqual(confidence_bucket(0.6), "0.55-0.65")
        self.assertEqual(confidence_bucket(0.8), "0.75+")


class TestPairClosedTrades(unittest.TestCase):
    def test_pairs_entry_exit(self):
        trades = [
            {
                "id": 1,
                "bot_id": "b1",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "is_exit": 0,
                "timestamp": "2026-01-01T10:00:00Z",
                "insight_snapshot": {
                    "signal": "BUY",
                    "score": 3,
                    "confidence": 0.7,
                    "sub_reports": {"risk": {"atr_regime": "normal"}},
                },
            },
            {
                "id": 2,
                "bot_id": "b1",
                "symbol": "BTCUSDT",
                "side": "SELL",
                "is_exit": 1,
                "pnl": 12.5,
                "timestamp": "2026-01-01T11:00:00Z",
            },
        ]
        closed = pair_closed_trades(trades, bot_timeframes={"b1": "5m"})
        self.assertEqual(len(closed), 1)
        self.assertTrue(closed[0].win)
        self.assertEqual(closed[0].pnl, 12.5)
        self.assertEqual(closed[0].timeframe, "5m")
        self.assertEqual(closed[0].atr_regime, "normal")

    def test_ignores_unpaired_exit(self):
        trades = [
            {
                "id": 2,
                "bot_id": "b1",
                "symbol": "ETHUSDT",
                "side": "SELL",
                "is_exit": 1,
                "pnl": -5.0,
                "timestamp": "2026-01-01T11:00:00Z",
            },
        ]
        self.assertEqual(pair_closed_trades(trades), [])


class TestSuggestThresholds(unittest.TestCase):
    def test_suggests_min_confidence_when_low_buckets_underperform(self):
        buckets = [
            {
                "symbol": "BTCUSDT",
                "confidence_bucket": "0.55-0.65",
                "score_bucket": "3",
                "sample_size": 8,
                "wilson_lower": 0.3,
            },
        ]
        hints = suggest_thresholds(buckets, min_samples=5)
        kinds = {h["kind"] for h in hints}
        self.assertIn("min_confidence", kinds)


class TestCalibrationIntegration(unittest.TestCase):
    def setUp(self):
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bot_trades")
        cursor.execute("DELETE FROM bot_logs")
        cursor.execute("DELETE FROM bots")
        cursor.execute(
            """
            INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("bot-cal-1", "CHART_AGENT", "BTCUSDT", "1m", "STOPPED", 1000.0, "{}"),
        )
        snap = json.dumps({
            "signal": "BUY",
            "score": 3,
            "confidence": 0.72,
            "sub_reports": {"risk": {"atr_regime": "normal"}},
        })
        cursor.execute(
            """
            INSERT INTO bot_trades
            (bot_id, symbol, side, quantity, price, pnl, is_exit, insight_snapshot, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("bot-cal-1", "BTCUSDT", "BUY", 1.0, 100.0, None, 0, snap, "2026-06-01T10:00:00Z"),
        )
        cursor.execute(
            """
            INSERT INTO bot_trades
            (bot_id, symbol, side, quantity, price, pnl, is_exit, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("bot-cal-1", "BTCUSDT", "SELL", 1.0, 110.0, 10.0, 1, "2026-06-01T11:00:00Z"),
        )
        cursor.execute(
            """
            INSERT INTO bot_logs (bot_id, level, message, meta)
            VALUES (?, ?, ?, ?)
            """,
            (
                "bot-cal-1",
                "WARN",
                "skip",
                json.dumps({
                    "event_type": "chart_agent_skip",
                    "symbol": "BTCUSDT",
                    "reject_reason": "trend score 0 does not align with BUY",
                }),
            ),
        )
        conn.commit()
        conn.close()

    def test_get_calibration_end_to_end(self):
        data = get_calibration(bot_id="bot-cal-1", min_samples=1)
        self.assertEqual(data["overall"]["closed_trades"], 1)
        self.assertEqual(data["overall"]["wins"], 1)
        self.assertGreaterEqual(len(data["buckets"]), 1)

    def test_live_filter_rejects_from_logs(self):
        live = aggregate_live_filter_rejects(bot_id="bot-cal-1")
        self.assertGreaterEqual(live["total"], 1)
        self.assertGreater(live["by_bucket"].get("trend", 0), 0)


class TestMetaLabelGate(unittest.TestCase):
    def setUp(self):
        init_db()
        get_calibration_store().invalidate()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bot_trades")
        cursor.execute("DELETE FROM bots")
        cursor.execute(
            """
            INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("bot-gate-1", "CHART_AGENT", "BTCUSDT", "1m", "RUNNING", 1000.0, "{}"),
        )
        losing_snap = json.dumps({
            "signal": "BUY",
            "score": 2,
            "confidence": 0.6,
            "sub_reports": {"risk": {"atr_regime": "normal"}},
        })
        for i in range(6):
            cursor.execute(
                """
                INSERT INTO bot_trades
                (bot_id, symbol, side, quantity, price, pnl, is_exit, insight_snapshot, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bot-gate-1", "BTCUSDT", "BUY", 1.0, 100.0, None, 0,
                    losing_snap, f"2026-06-0{i+1}T10:00:00Z",
                ),
            )
            cursor.execute(
                """
                INSERT INTO bot_trades
                (bot_id, symbol, side, quantity, price, pnl, is_exit, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bot-gate-1", "BTCUSDT", "SELL", 1.0, 95.0, -5.0, 1,
                    f"2026-06-0{i+1}T11:00:00Z",
                ),
            )
        conn.commit()
        conn.close()
        get_calibration_store().invalidate("bot-gate-1")

    def test_blocks_underperforming_bucket(self):
        insight = {
            "score": 2,
            "confidence": 0.6,
            "sub_reports": {"risk": {"atr_regime": "normal"}},
        }
        cfg = {
            "calibration_gate_enabled": True,
            "calibration_min_samples": 5,
            "calibration_min_wilson": 0.45,
        }
        reason = check_meta_label_gate(
            insight,
            cfg,
            symbol="BTCUSDT",
            timeframe="1m",
            signal="BUY",
            bot_id="bot-gate-1",
        )
        self.assertIsNotNone(reason)
        self.assertIn("calibration gate", reason.lower())

    def test_allows_when_gate_disabled(self):
        insight = {
            "score": 2,
            "confidence": 0.6,
            "sub_reports": {"risk": {"atr_regime": "normal"}},
        }
        cfg = {"calibration_gate_enabled": False}
        reason = check_meta_label_gate(
            insight,
            cfg,
            symbol="BTCUSDT",
            timeframe="1m",
            signal="BUY",
            bot_id="bot-gate-1",
        )
        self.assertIsNone(reason)

    def test_setup_bucket_key_matches_closed_trade(self):
        trades = [
            {
                "id": 1,
                "bot_id": "b1",
                "symbol": "ETHUSDT",
                "side": "BUY",
                "is_exit": 0,
                "timestamp": "2026-01-01T10:00:00Z",
                "insight_snapshot": {
                    "score": 3,
                    "confidence": 0.72,
                    "sub_reports": {"risk": {"atr_regime": "elevated"}},
                },
            },
            {
                "id": 2,
                "bot_id": "b1",
                "symbol": "ETHUSDT",
                "side": "SELL",
                "is_exit": 1,
                "pnl": 1.0,
                "timestamp": "2026-01-01T11:00:00Z",
            },
        ]
        closed = pair_closed_trades(trades, bot_timeframes={"b1": "5m"})
        insight = {
            "score": 3,
            "confidence": 0.72,
            "sub_reports": {"risk": {"atr_regime": "elevated"}},
        }
        self.assertEqual(
            setup_bucket_key(symbol="ETHUSDT", timeframe="5m", side="BUY", insight=insight),
            closed[0].bucket_key(),
        )


class TestApplySuggestions(unittest.TestCase):
    def test_build_config_patch_merges_thresholds(self):
        suggestions = [
            {
                "symbol": "BTCUSDT",
                "kind": "min_confidence",
                "suggested_min_confidence": 0.65,
            },
            {
                "symbol": "BTCUSDT",
                "kind": "min_score",
                "suggested_min_score": 3,
            },
            {
                "symbol": "BTCUSDT",
                "kind": "block_elevated_vol",
            },
        ]
        result = build_config_patch_from_suggestions(suggestions, symbol="BTCUSDT")
        patch = result["patch"]
        self.assertEqual(patch["min_confidence"], 0.65)
        self.assertEqual(patch["min_score"], 3)
        self.assertTrue(patch["block_elevated_vol"])
        self.assertTrue(patch["calibration_gate_enabled"])

    def test_calibration_store_builds_index(self):
        init_db()
        get_calibration_store().invalidate()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bot_trades")
        cursor.execute("DELETE FROM bots")
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("bot-idx", "CHART_AGENT", "SOLUSDT", "1m", "RUNNING", 1000.0, "{}"),
        )
        snap = json.dumps({"score": 3, "confidence": 0.7, "sub_reports": {"risk": {"atr_regime": "normal"}}})
        cursor.execute(
            "INSERT INTO bot_trades (bot_id, symbol, side, quantity, price, is_exit, insight_snapshot, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("bot-idx", "SOLUSDT", "BUY", 1.0, 10.0, 0, snap, "2026-06-01T10:00:00Z"),
        )
        cursor.execute(
            "INSERT INTO bot_trades (bot_id, symbol, side, quantity, price, pnl, is_exit, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("bot-idx", "SOLUSDT", "SELL", 1.0, 11.0, 1.0, 1, "2026-06-01T11:00:00Z"),
        )
        conn.commit()
        conn.close()

        store = CalibrationStore(ttl_sec=60)
        key = setup_bucket_key(
            symbol="SOLUSDT",
            timeframe="1m",
            side="BUY",
            insight={"score": 3, "confidence": 0.7, "sub_reports": {"risk": {"atr_regime": "normal"}}},
        )
        stats = store.lookup("bot-idx", key)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["sample_size"], 1)
        self.assertEqual(stats["win_count"], 1)

    def test_compute_apply_patch_empty_when_no_suggestions(self):
        init_db()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bot_trades")
        cursor.execute("DELETE FROM bots")
        cursor.execute(
            "INSERT INTO bots (id, strategy, symbol, timeframe, status, allocation, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("bot-empty", "CHART_AGENT", "XRPUSDT", "1m", "STOPPED", 1000.0, "{}"),
        )
        conn.commit()
        conn.close()
        result = compute_calibration_apply_patch("bot-empty")
        self.assertEqual(result["patch"], {})


if __name__ == "__main__":
    unittest.main()
