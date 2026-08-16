"""Tests for the closed-loop agent decision evaluator (Sprint 4)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

os.environ.setdefault("TERMINAL_MODE", "SIMULATED")
os.environ["DATABASE_URL"] = ""
# Never touch profile DBs (trading-alpaca.db / trading.db) from unit tests.
os.environ.pop("TERMINAL_PROFILE", None)
_TEST_DIR = tempfile.mkdtemp()
os.environ["SQLITE_DB_PATH"] = os.path.join(_TEST_DIR, "decision_eval_test.db")

import app.config as app_config  # noqa: E402
import app.db.connection as db_conn  # noqa: E402

db_conn.DB_PATH = os.environ["SQLITE_DB_PATH"]
db_conn.DB_DRIVER = "sqlite"
db_conn._DATABASE_URL = ""
db_conn._pool = None  # drop any pool bound before path rebind
app_config.DB_PATH = db_conn.DB_PATH
assert os.path.basename(db_conn.DB_PATH).lower() not in {
    "trading-alpaca.db", "trading-ib.db", "trading-massive.db", "trading-sim.db", "trading.db",
}, db_conn.DB_PATH

from app.database import get_connection, init_db  # noqa: E402
from app.services.agent.decision_eval import (  # noqa: E402
    advisor_confidence_weight,
    get_decision_scores,
    run_decision_eval,
)

NOW = 1_800_000_000.0  # fixed reference time; well past any real fixture data


def _ts(epoch: float) -> str:
    """SQLite CURRENT_TIMESTAMP-style text for an epoch second."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _insert_bot(bot_id: str, symbol: str) -> None:
    conn = get_connection()
    try:
        conn.cursor().execute(
            """
            INSERT OR IGNORE INTO bots (id, strategy, symbol, timeframe, status, allocation, config)
            VALUES (?, 'CHART_AGENT', ?, '1m', 'RUNNING', 1000, '{}')
            """,
            (bot_id, symbol),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_veto_log(bot_id: str, symbol: str, *, decided_at: float, side: str, price: float) -> None:
    meta = {
        "event_type": "pretrade_veto",
        "symbol": symbol,
        "side": side,
        "price": price,
        "vetoes": ["price_gap_anomaly: 3.10% gap"],
    }
    conn = get_connection()
    try:
        conn.cursor().execute(
            "INSERT INTO bot_logs (bot_id, level, message, timestamp, meta) VALUES (?, 'WARN', ?, ?, ?)",
            (bot_id, f"Pre-Trade Intel VETO: {meta['vetoes'][0]}", _ts(decided_at), json.dumps(meta)),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_event(event_type: str, source: str, payload: dict, ts: float) -> None:
    conn = get_connection()
    try:
        conn.cursor().execute(
            """
            INSERT INTO agent_events (event_type, source, bot_id, payload, reasoning, ts, created_at)
            VALUES (?, ?, ?, ?, NULL, ?, ?)
            """,
            (event_type, source, payload.get("bot_id"), json.dumps(payload), ts, _ts(ts)),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_exit(bot_id: str, *, symbol: str, pnl: float, ts: float) -> None:
    conn = get_connection()
    try:
        conn.cursor().execute(
            """
            INSERT INTO bot_trades (bot_id, symbol, side, quantity, price, pnl, is_exit, timestamp)
            VALUES (?, ?, 'SELL', 1, 100, ?, 1, ?)
            """,
            (bot_id, symbol, pnl, _ts(ts)),
        )
        conn.commit()
    finally:
        conn.close()


def _bars(closes: list[tuple[float, float]]) -> list[dict]:
    """Synthetic 1m bars from (time, close) pairs."""
    return [
        {"time": int(t), "open": c, "high": c, "low": c, "close": c, "volume": 1.0}
        for t, c in closes
    ]


def _provider_from(bars: list[dict]):
    def _provide(symbol: str, from_ts: float, to_ts: float) -> list[dict]:
        return [b for b in bars if from_ts <= b["time"] <= to_ts]

    return _provide


def _outcome_rows(decision_type: str | None = None, bot_id: str | None = None) -> list[dict]:
    clauses: list[str] = []
    params: list = []
    if decision_type:
        clauses.append("decision_type = ?")
        params.append(decision_type)
    if bot_id:
        clauses.append("bot_id = ?")
        params.append(bot_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = get_connection()
    try:
        rows = conn.cursor().execute(
            f"""
            SELECT decision_key, decision_type, agent, bot_id, symbol, decided_at,
                   evaluated_at, score, outcome, detail
            FROM agent_decision_outcomes {where}
            ORDER BY id
            """,
            tuple(params),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class TestVetoScoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_correct_veto_scores_plus_one(self):
        _insert_bot("bot-veto-ok", "AAPL")
        decided = NOW - 7200.0  # 2h ago: past min_age, inside max_age
        _insert_veto_log("bot-veto-ok", "AAPL", decided_at=decided, side="BUY", price=100.0)
        # Price falls 3% over the veto horizon → blocking the BUY was right.
        bars = _bars([(decided - 60, 100.0), (decided, 100.0), (decided + 3600, 97.0)])
        stats = run_decision_eval(now=NOW, bar_provider=_provider_from(bars))
        self.assertEqual(stats["registered"], 1)
        self.assertEqual(stats["graded"], 1)

        rows = _outcome_rows("veto", "bot-veto-ok")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["agent"], "PRETRADE_INTEL")
        self.assertEqual(row["outcome"], "correct")
        self.assertEqual(row["score"], 1.0)
        detail = json.loads(row["detail"])
        self.assertEqual(detail["side"], "BUY")
        self.assertAlmostEqual(detail["counterfactual_move_pct"], -3.0, places=3)
        self.assertEqual(detail["price_at"], 100.0)
        self.assertEqual(detail["price_after"], 97.0)

    def test_wrong_veto_scores_minus_one(self):
        _insert_bot("bot-veto-bad", "AAPL")
        decided = NOW - 7500.0
        _insert_veto_log("bot-veto-bad", "AAPL", decided_at=decided, side="BUY", price=100.0)
        # Price rallies 3% → the veto cost money.
        bars = _bars([(decided - 60, 100.0), (decided, 100.0), (decided + 3600, 103.0)])
        run_decision_eval(now=NOW, bar_provider=_provider_from(bars))

        rows = _outcome_rows("veto", "bot-veto-bad")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "wrong")
        self.assertEqual(rows[0]["score"], -1.0)

    def test_flat_move_scores_zero(self):
        _insert_bot("bot-veto-flat", "AAPL")
        decided = NOW - 7800.0
        _insert_veto_log("bot-veto-flat", "AAPL", decided_at=decided, side="SELL", price=100.0)
        bars = _bars([(decided - 60, 100.0), (decided, 100.0), (decided + 3600, 100.01)])
        run_decision_eval(now=NOW, bar_provider=_provider_from(bars))

        rows = _outcome_rows("veto", "bot-veto-flat")
        self.assertEqual(rows[0]["outcome"], "flat")
        self.assertEqual(rows[0]["score"], 0.0)

    def test_missing_bars_stays_pending_then_expires(self):
        _insert_bot("bot-veto-miss", "AAPL")
        decided = NOW - 8100.0
        _insert_veto_log("bot-veto-miss", "AAPL", decided_at=decided, side="BUY", price=100.0)
        empty = _provider_from([])
        stats = run_decision_eval(now=NOW, bar_provider=empty)
        self.assertEqual(stats["graded"], 0)

        row = _outcome_rows("veto", "bot-veto-miss")[0]
        self.assertIsNone(row["evaluated_at"])

        # Once older than max_age the decision is closed out, never re-graded.
        later = decided + 86400.0 + 60.0
        stats = run_decision_eval(now=later, bar_provider=empty)
        self.assertEqual(stats["expired"], 1)
        row = _outcome_rows("veto", "bot-veto-miss")[0]
        self.assertEqual(row["outcome"], "insufficient_data")
        self.assertIsNotNone(row["evaluated_at"])


class TestRotationAndPatchScoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_rotation_improvement_scores_positive(self):
        _insert_bot("bot-rot", "MSFT")
        decided = NOW - 7200.0
        _insert_event(
            "REGIME_CHANGED",
            "REGIME_ROTATION",
            {
                "bot_id": "bot-rot",
                "symbol": "MSFT",
                "old_strategy": "BRS_SCALPING",
                "new_strategy": "TREND_RIDER",
                "new_regime": "trending",
            },
            decided,
        )
        for ts, pnl in ((decided - 1000, -1.0), (decided - 500, -1.0)):
            _insert_exit("bot-rot", symbol="MSFT", pnl=pnl, ts=ts)
        for ts, pnl in ((decided + 1000, 2.0), (decided + 2000, 2.0)):
            _insert_exit("bot-rot", symbol="MSFT", pnl=pnl, ts=ts)

        stats = run_decision_eval(now=NOW, bar_provider=_provider_from([]))
        self.assertGreaterEqual(stats["graded"], 1)

        rows = _outcome_rows("rotation", "bot-rot")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["agent"], "REGIME_ROTATION")
        self.assertEqual(row["outcome"], "improved")
        self.assertGreater(row["score"], 0.0)
        detail = json.loads(row["detail"])
        self.assertEqual(detail["new_strategy"], "TREND_RIDER")
        self.assertEqual(detail["before"]["avg_pnl"], -1.0)
        self.assertEqual(detail["after"]["avg_pnl"], 2.0)
        self.assertEqual(detail["win_rate_delta"], 1.0)

    def test_patch_scoring_before_after(self):
        _insert_bot("bot-patch", "TSLA")
        decided = NOW - 7200.0
        _insert_event(
            "POSTTRADE_LESSON",
            "POSTTRADE_LEARNER",
            {
                "bot_id": "bot-patch",
                "symbol": "TSLA",
                "lesson": {
                    "applied": True,
                    "config_patch": {"min_confidence": 0.6},
                    "outcome_class": "clean_loss",
                },
            },
            decided,
        )
        for ts, pnl in ((decided - 1000, -2.0), (decided - 500, -2.0)):
            _insert_exit("bot-patch", symbol="TSLA", pnl=pnl, ts=ts)
        for ts, pnl in ((decided + 1000, -1.0), (decided + 2000, 1.0)):
            _insert_exit("bot-patch", symbol="TSLA", pnl=pnl, ts=ts)

        run_decision_eval(now=NOW, bar_provider=_provider_from([]))
        rows = _outcome_rows("patch", "bot-patch")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["agent"], "POSTTRADE_LEARNER")
        self.assertEqual(row["outcome"], "improved")
        self.assertEqual(row["score"], 2.0)  # avg pnl -2.0 → 0.0
        detail = json.loads(row["detail"])
        self.assertEqual(detail["patch"], {"min_confidence": 0.6})

    def test_unapplied_patch_lesson_not_registered(self):
        decided = NOW - 7200.0
        _insert_event(
            "POSTTRADE_LESSON",
            "POSTTRADE_LEARNER",
            {
                "bot_id": "bot-patch-na",
                "symbol": "TSLA",
                "lesson": {"applied": False, "config_patch": {"min_confidence": 0.6}},
            },
            decided,
        )
        run_decision_eval(now=NOW, bar_provider=_provider_from([]))
        self.assertEqual(_outcome_rows("patch", "bot-patch-na"), [])

    def test_rotation_without_trades_stays_pending(self):
        _insert_bot("bot-rot-empty", "NVDA")
        decided = NOW - 7200.0
        _insert_event(
            "REGIME_CHANGED",
            "REGIME_ROTATION",
            {"bot_id": "bot-rot-empty", "symbol": "NVDA", "new_strategy": "TREND_RIDER"},
            decided,
        )
        run_decision_eval(now=NOW, bar_provider=_provider_from([]))
        row = _outcome_rows("rotation", "bot-rot-empty")[0]
        self.assertIsNone(row["evaluated_at"])


class TestPauseScoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_pause_saved_money_scores_plus_one(self):
        _insert_bot("bot-pause-saved", "AMD")
        decided = NOW - 20000.0  # 5.5h ago: beyond the 4h pause horizon
        _insert_event(
            "BOT_PAUSED",
            "RISK_SENTINEL",
            {"bot_id": "bot-pause-saved", "reason": "loss_streak"},
            decided,
        )
        # Symbol keeps dropping after the pause → pause saved money.
        bars = _bars([(decided - 60, 50.0), (decided, 50.0), (decided + 14400, 47.0)])
        stats = run_decision_eval(now=NOW, bar_provider=_provider_from(bars))
        self.assertGreaterEqual(stats["graded"], 1)

        rows = _outcome_rows("pause", "bot-pause-saved")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["agent"], "RISK_SENTINEL")
        self.assertEqual(row["symbol"], "AMD")  # resolved via bots table
        self.assertEqual(row["outcome"], "saved")
        self.assertEqual(row["score"], 1.0)
        detail = json.loads(row["detail"])
        self.assertAlmostEqual(detail["move_pct"], -6.0, places=3)

    def test_pause_premature_scores_minus_one(self):
        _insert_bot("bot-pause-early", "INTC")
        decided = NOW - 20000.0
        _insert_event(
            "BOT_PAUSED",
            "RISK_SENTINEL",
            {"bot_id": "bot-pause-early", "reason": "drawdown_velocity_spike"},
            decided,
        )
        bars = _bars([(decided - 60, 30.0), (decided, 30.0), (decided + 14400, 32.0)])
        run_decision_eval(now=NOW, bar_provider=_provider_from(bars))

        rows = _outcome_rows("pause", "bot-pause-early")
        self.assertEqual(rows[0]["outcome"], "premature")
        self.assertEqual(rows[0]["score"], -1.0)


class TestIdempotencyRetentionAndSummary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_no_double_eval(self):
        _insert_bot("bot-idem", "AAPL")
        decided = NOW - 7200.0
        _insert_veto_log("bot-idem", "AAPL", decided_at=decided, side="BUY", price=100.0)
        bars = _bars([(decided - 60, 100.0), (decided, 100.0), (decided + 3600, 96.0)])

        first = run_decision_eval(now=NOW, bar_provider=_provider_from(bars))
        self.assertEqual(first["graded"], 1)
        row_before = _outcome_rows(bot_id="bot-idem")[0]
        self.assertIsNotNone(row_before["evaluated_at"])

        second = run_decision_eval(now=NOW + 3600.0, bar_provider=_provider_from(bars))
        self.assertEqual(second["graded"], 0)
        rows_after = _outcome_rows(bot_id="bot-idem")
        self.assertEqual(len(rows_after), 1)
        self.assertEqual(rows_after[0]["evaluated_at"], row_before["evaluated_at"])
        self.assertEqual(rows_after[0]["score"], row_before["score"])

    def test_retention_prunes_old_rows(self):
        old_decided = NOW - 40 * 86400.0
        conn = get_connection()
        try:
            conn.cursor().execute(
                """
                INSERT INTO agent_decision_outcomes
                    (decision_key, decision_type, agent, bot_id, symbol,
                     decided_at, evaluated_at, score, outcome, detail, created_at)
                VALUES ('veto:ancient', 'veto', 'PRETRADE_INTEL', 'bot-x', 'AAPL',
                        ?, ?, 1.0, 'correct', '{}', ?)
                """,
                (old_decided, old_decided + 3600, _ts(old_decided)),
            )
            conn.commit()
        finally:
            conn.close()

        stats = run_decision_eval(now=NOW, bar_provider=_provider_from([]))
        self.assertGreaterEqual(stats["pruned"], 1)
        self.assertEqual(
            [r for r in _outcome_rows() if r["decision_key"] == "veto:ancient"],
            [],
        )

    def test_summary_and_advisor_weight(self):
        _insert_bot("bot-sum", "AAPL")
        baseline = get_decision_scores("PRETRADE_INTEL")
        base_graded = sum(int(r.get("graded") or 0) for r in baseline["summary"])

        decided = NOW - 7200.0
        # Two correct vetoes + one wrong.
        bars: list[tuple[float, float]] = []
        for i, end_px in enumerate((96.0, 95.0, 104.0)):
            d = decided - i * 10.0
            _insert_veto_log("bot-sum", "AAPL", decided_at=d, side="BUY", price=100.0)
            bars.extend([(d - 60, 100.0), (d, 100.0), (d + 3600, end_px)])
        run_decision_eval(now=NOW, bar_provider=_provider_from(_bars(bars)))

        rows = _outcome_rows("veto", "bot-sum")
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            sorted(r["outcome"] for r in rows),
            ["correct", "correct", "wrong"],
        )

        data = get_decision_scores("PRETRADE_INTEL")
        self.assertTrue(data["summary"])
        veto_summary = [r for r in data["summary"] if r["decision_type"] == "veto"][0]
        self.assertEqual(veto_summary["graded"], base_graded + 3)

        weight = advisor_confidence_weight("PRETRADE_INTEL", decision_type="veto", min_graded=3)
        self.assertIsNotNone(weight)
        self.assertAlmostEqual(weight, veto_summary["accuracy"], places=3)
        # Below min_graded → no opinion.
        self.assertIsNone(advisor_confidence_weight("PRETRADE_INTEL", min_graded=100000))
        self.assertIsNone(advisor_confidence_weight("NO_SUCH_AGENT"))


class TestDecisionEvalApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        from starlette.testclient import TestClient

        from app.api.http.app import create_http_app
        from app.api.state import AppState

        # Seed one graded decision so the endpoint has data regardless of
        # test ordering (classes run alphabetically).
        _insert_bot("bot-api-eval", "AMD")
        decided = NOW - 20000.0
        _insert_event(
            "BOT_PAUSED",
            "RISK_SENTINEL",
            {"bot_id": "bot-api-eval", "reason": "loss_streak"},
            decided,
        )
        bars = _bars([(decided - 60, 50.0), (decided, 50.0), (decided + 14400, 48.0)])
        run_decision_eval(now=NOW, bar_provider=_provider_from(bars))

        oms = MagicMock()
        bot_manager = MagicMock()
        manager = MagicMock()
        manager.connected_clients = set()
        state = AppState(oms=oms, manager=manager, bot_manager=bot_manager,
                         backtester=None, chart_analyst=None)
        cls.client = TestClient(create_http_app(state))

    def test_eval_endpoint_shape(self):
        resp = self.client.get("/api/v1/agent/eval")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        payload = body["eval"]
        for key in ("summary", "recent", "pending"):
            self.assertIn(key, payload)
        self.assertIsInstance(payload["summary"], list)
        self.assertIsInstance(payload["recent"], list)
        self.assertIsInstance(payload["pending"], int)
        agents = {row["agent"] for row in payload["summary"]}
        self.assertIn("RISK_SENTINEL", agents)
        recent = payload["recent"][0]
        for key in (
            "decision_key", "decision_type", "agent", "bot_id", "symbol",
            "decided_at", "evaluated_at", "score", "outcome", "detail",
        ):
            self.assertIn(key, recent)

    def test_eval_endpoint_agent_filter(self):
        resp = self.client.get("/api/v1/agent/eval?agent=RISK_SENTINEL")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["eval"]
        self.assertTrue(payload["summary"])
        self.assertTrue(all(r["agent"] == "RISK_SENTINEL" for r in payload["summary"]))
        self.assertTrue(all(r["agent"] == "RISK_SENTINEL" for r in payload["recent"]))

        resp = self.client.get("/api/v1/agent/eval?agent=NOPE&limit=abc")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()["eval"]
        self.assertEqual(payload["summary"], [])
        self.assertEqual(payload["recent"], [])


if __name__ == "__main__":
    unittest.main()
