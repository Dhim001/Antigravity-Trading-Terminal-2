"""EXECUTION_RISK_INTELLIGENCE_PLAN Phase 1 — execution TCA: arrival benchmark,
implementation-shortfall decomposition, persistence, retention, and the
pending-fill arrival round-trip used by live-fill reconciliation."""

import os
import sqlite3
import tempfile

import pytest

from app import config
from app.services.bots import analytics as bot_analytics
from app.services.bots import execution_tca


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_tca_db(monkeypatch):
    """Point execution_tca + analytics at a fresh sqlite with the TCA and
    pending-fill tables."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE execution_quality_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT NOT NULL,
            symbol TEXT,
            strategy TEXT,
            side TEXT,
            is_exit INTEGER DEFAULT 0,
            exec_algo TEXT,
            order_id TEXT,
            signal_id TEXT,
            decision_price REAL,
            arrival_price REAL,
            arrival_bid REAL,
            arrival_ask REAL,
            requested_qty REAL,
            filled_qty REAL,
            avg_fill_price REAL,
            is_bps REAL,
            delay_bps REAL,
            spread_bps REAL,
            impact_bps REAL,
            opp_bps REAL,
            fees REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE bot_pending_fills (
            id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            order_id TEXT,
            symbol TEXT,
            side TEXT,
            quantity REAL,
            signal_price REAL,
            signal_id TEXT,
            is_exit INTEGER DEFAULT 0,
            entry_price REAL,
            insight_snapshot TEXT,
            arrival_price REAL,
            arrival_bid REAL,
            arrival_ask REAL,
            exec_algo TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

    def fake_get_connection():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(execution_tca, "get_connection", fake_get_connection)
    monkeypatch.setattr(bot_analytics, "get_connection", fake_get_connection)
    monkeypatch.setattr(config, "EXEC_QUALITY_LOG_ENABLED", True)
    yield path
    os.unlink(path)


def _rows(path, sql="SELECT * FROM execution_quality_log ORDER BY id"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    conn.close()
    return rows


class _FakeFeed:
    def __init__(self, symbols):
        self._symbols = symbols


# ---------------------------------------------------------------------------
# capture_arrival
# ---------------------------------------------------------------------------

class TestCaptureArrival:
    def test_reads_mark_and_quote_from_feed(self):
        feed = _FakeFeed({"AAPL": {"price": 201.5, "bid": 201.4, "ask": 201.6}})
        snap = execution_tca.capture_arrival(feed, "AAPL", 200.0)
        assert snap == {"arrival_price": 201.5, "arrival_bid": 201.4, "arrival_ask": 201.6}

    def test_missing_symbol_falls_back_to_signal_price(self):
        feed = _FakeFeed({})
        snap = execution_tca.capture_arrival(feed, "MSFT", 305.25)
        assert snap["arrival_price"] == 305.25
        assert snap["arrival_bid"] is None

    def test_no_feed_falls_back(self):
        snap = execution_tca.capture_arrival(None, "BTC/USD", 50_000.0)
        assert snap["arrival_price"] == 50_000.0

    def test_symbol_case_insensitive(self):
        feed = _FakeFeed({"ETHUSDT": {"price": 3500.0}})
        snap = execution_tca.capture_arrival(feed, "ethusdt", 0.0)
        assert snap["arrival_price"] == 3500.0


# ---------------------------------------------------------------------------
# compute_is — pure math
# ---------------------------------------------------------------------------

class TestComputeIs:
    def test_buy_cost_positive_when_fill_above_decision(self):
        out = execution_tca.compute_is(
            side="BUY", decision_price=100.0, arrival_price=100.10,
            avg_fill_price=100.20, requested_qty=10, filled_qty=10,
        )
        # delay: buy drifted up 0.10/100 = +10bps (cost)
        assert out["delay_bps"] == pytest.approx(10.0, abs=0.01)
        # IS: (100.20 - 100)/100 = +20bps
        assert out["is_bps"] == pytest.approx(20.0, abs=0.01)
        # impact (= exec vs arrival, no quote): (100.20-100.10)/100.10 ≈ 9.99bps
        assert out["impact_bps"] == pytest.approx(9.99, abs=0.01)
        assert out["spread_bps"] is None
        assert out["opp_bps"] is None

    def test_sell_cost_positive_when_fill_below_arrival(self):
        out = execution_tca.compute_is(
            side="SELL", decision_price=100.0, arrival_price=100.0,
            avg_fill_price=99.80, requested_qty=5, filled_qty=5,
        )
        # sold 0.20 below arrival = +20bps cost
        assert out["is_bps"] == pytest.approx(20.0, abs=0.01)
        assert out["impact_bps"] == pytest.approx(20.0, abs=0.01)
        assert out["delay_bps"] == pytest.approx(0.0, abs=1e-9)

    def test_price_improvement_is_negative_impact(self):
        out = execution_tca.compute_is(
            side="BUY", decision_price=100.0, arrival_price=100.0,
            avg_fill_price=99.95, requested_qty=1, filled_qty=1,
        )
        assert out["is_bps"] == pytest.approx(-5.0, abs=0.01)
        assert out["impact_bps"] == pytest.approx(-5.0, abs=0.01)

    def test_spread_split_from_quote(self):
        # arrival mid 100.10 with 0.04-wide spread -> half-spread ≈ 1.998bps
        out = execution_tca.compute_is(
            side="BUY", decision_price=100.0, arrival_price=100.10,
            arrival_bid=100.08, arrival_ask=100.12,
            avg_fill_price=100.20, requested_qty=1, filled_qty=1,
        )
        assert out["spread_bps"] == pytest.approx(0.02 / 100.10 * 10_000, abs=0.01)
        # impact = exec - spread
        exec_bps = (100.20 - 100.10) / 100.10 * 10_000
        assert out["impact_bps"] == pytest.approx(exec_bps - out["spread_bps"], abs=0.01)

    def test_partial_fill_opportunity_cost_weighted(self):
        out = execution_tca.compute_is(
            side="BUY", decision_price=100.0, arrival_price=100.0,
            avg_fill_price=100.0, requested_qty=100, filled_qty=50,
            end_mark=101.0,
        )
        # half the order unfilled; mark ran +1% = +100bps on the unfilled half
        assert out["opp_bps"] == pytest.approx(50.0, abs=0.01)
        assert out["unfilled_qty"] == pytest.approx(50.0)

    def test_full_fill_has_no_opportunity_cost(self):
        out = execution_tca.compute_is(
            side="BUY", decision_price=100.0, arrival_price=100.0,
            avg_fill_price=100.0, requested_qty=10, filled_qty=10,
            end_mark=105.0,
        )
        assert out["opp_bps"] is None
        assert out["unfilled_qty"] == pytest.approx(0.0)

    def test_no_fill_no_mark_is_all_none(self):
        out = execution_tca.compute_is(
            side="BUY", decision_price=100.0, arrival_price=100.0,
            avg_fill_price=None, requested_qty=10, filled_qty=0,
        )
        assert out["is_bps"] is None
        assert out["opp_bps"] is None

    def test_garbage_inputs_do_not_raise(self):
        out = execution_tca.compute_is(
            side="BUY", decision_price=None, arrival_price=0,
            avg_fill_price="nope", requested_qty=None, filled_qty=None,
        )
        assert out["is_bps"] is None


# ---------------------------------------------------------------------------
# record_execution persistence
# ---------------------------------------------------------------------------

class TestRecordExecution:
    def test_writes_row_with_decomposition(self, tmp_tca_db):
        execution_tca.record_execution(
            bot_id="bot-1", symbol="AAPL", strategy="TREND_RIDERS", side="BUY",
            is_exit=False, exec_algo="single", order_id="o-1", signal_id="s-1",
            decision_price=100.0,
            arrival={"arrival_price": 100.10, "arrival_bid": 100.08, "arrival_ask": 100.12},
            requested_qty=10, filled_qty=10, avg_fill_price=100.20,
        )
        rows = _rows(tmp_tca_db)
        assert len(rows) == 1
        r = rows[0]
        assert r["bot_id"] == "bot-1"
        assert r["exec_algo"] == "single"
        assert r["is_bps"] == pytest.approx(20.0, abs=0.01)
        assert r["delay_bps"] == pytest.approx(10.0, abs=0.01)
        assert r["spread_bps"] == pytest.approx(0.02 / 100.10 * 10_000, abs=0.01)

    def test_sliced_partial_fill_records_opportunity(self, tmp_tca_db):
        execution_tca.record_execution(
            bot_id="bot-2", symbol="ETHUSDT", strategy="ML_SIGNAL_BOOST", side="BUY",
            is_exit=False, exec_algo="vwap", order_id="o-2", signal_id="s-2",
            decision_price=3500.0, arrival={"arrival_price": 3500.0},
            requested_qty=2.0, filled_qty=1.0, avg_fill_price=3500.0,
            end_mark=3535.0,
        )
        rows = _rows(tmp_tca_db)
        assert len(rows) == 1
        # unfilled half ran +1% ⇒ +50bps opportunity
        assert rows[0]["opp_bps"] == pytest.approx(50.0, abs=0.1)
        assert rows[0]["filled_qty"] == pytest.approx(1.0)
        assert rows[0]["requested_qty"] == pytest.approx(2.0)

    def test_disabled_flag_skips_write(self, tmp_tca_db, monkeypatch):
        monkeypatch.setattr(config, "EXEC_QUALITY_LOG_ENABLED", False)
        execution_tca.record_execution(
            bot_id="bot-3", symbol="AAPL", strategy="S", side="BUY",
            is_exit=False, exec_algo="single", order_id="o", signal_id="s",
            decision_price=100.0, arrival={"arrival_price": 100.0},
            requested_qty=1, filled_qty=1, avg_fill_price=100.0,
        )
        assert _rows(tmp_tca_db) == []

    def test_nothing_measurable_skips_row(self, tmp_tca_db):
        execution_tca.record_execution(
            bot_id="bot-4", symbol="AAPL", strategy="S", side="BUY",
            is_exit=False, exec_algo="single", order_id="o", signal_id="s",
            decision_price=None, arrival={"arrival_price": None},
            requested_qty=1, filled_qty=0, avg_fill_price=None,
        )
        assert _rows(tmp_tca_db) == []

    def test_db_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(config, "EXEC_QUALITY_LOG_ENABLED", True)
        monkeypatch.setattr(
            execution_tca, "get_connection",
            lambda: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        # Must not raise into the order path.
        execution_tca.record_execution(
            bot_id="bot-5", symbol="AAPL", strategy="S", side="BUY",
            is_exit=False, exec_algo="single", order_id="o", signal_id="s",
            decision_price=100.0, arrival={"arrival_price": 100.0},
            requested_qty=1, filled_qty=1, avg_fill_price=100.0,
        )


# ---------------------------------------------------------------------------
# Pending-fill arrival round-trip (live reconciliation input)
# ---------------------------------------------------------------------------

class TestPendingFillArrival:
    def test_arrival_survives_round_trip(self, tmp_tca_db):
        bot_analytics.record_pending_fill(
            "bot-9", "order-9", "AAPL", "BUY", 10, 200.0,
            signal_id="sig-9", is_exit=False,
            arrival_price=200.5, arrival_bid=200.4, arrival_ask=200.6,
            exec_algo="pov",
        )
        pending = bot_analytics.list_pending_fills()
        assert len(pending) == 1
        p = pending[0]
        assert p["arrival_price"] == pytest.approx(200.5)
        assert p["arrival_bid"] == pytest.approx(200.4)
        assert p["arrival_ask"] == pytest.approx(200.6)
        assert p["exec_algo"] == "pov"

    def test_legacy_call_without_arrival_defaults_null(self, tmp_tca_db):
        bot_analytics.record_pending_fill(
            "bot-10", "order-10", "MSFT", "SELL", 5, 300.0,
        )
        p = bot_analytics.list_pending_fills()[0]
        assert p["arrival_price"] is None
        assert p["exec_algo"] is None


# ---------------------------------------------------------------------------
# Retention + summary
# ---------------------------------------------------------------------------

class TestRetention:
    def test_prune_deletes_old_rows(self, tmp_tca_db):
        conn = sqlite3.connect(tmp_tca_db)
        conn.execute(
            "INSERT INTO execution_quality_log (bot_id, side, is_bps, created_at) "
            "VALUES ('b', 'BUY', 10, datetime('now', '-40 days'))"
        )
        conn.execute(
            "INSERT INTO execution_quality_log (bot_id, side, is_bps, created_at) "
            "VALUES ('b', 'BUY', 20, datetime('now'))"
        )
        conn.commit()
        conn.close()
        deleted = execution_tca.prune_execution_quality_log(30)
        assert deleted == 1
        rows = _rows(tmp_tca_db)
        assert len(rows) == 1
        assert rows[0]["is_bps"] == pytest.approx(20.0)

    def test_prune_caps_max_rows_oldest_first(self, tmp_tca_db):
        conn = sqlite3.connect(tmp_tca_db)
        for i in range(5):
            conn.execute(
                "INSERT INTO execution_quality_log (bot_id, side, is_bps, created_at) "
                "VALUES ('b', 'BUY', ?, datetime('now', ?))",
                (float(i), f"+{i} seconds"),
            )
        conn.commit()
        conn.close()
        deleted = execution_tca.prune_execution_quality_log(0, max_rows=3)
        assert deleted == 2
        rows = _rows(tmp_tca_db)
        assert [r["is_bps"] for r in rows] == [2.0, 3.0, 4.0]

    def test_prune_failure_returns_zero(self, monkeypatch):
        monkeypatch.setattr(
            execution_tca, "get_connection",
            lambda: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        assert execution_tca.prune_execution_quality_log(30, max_rows=10) == 0


class TestSummary:
    def test_groups_by_algo_and_side(self, tmp_tca_db):
        for algo, side, is_bps in [
            ("single", "BUY", 10.0), ("single", "BUY", 20.0), ("vwap", "SELL", -5.0),
        ]:
            execution_tca.record_execution(
                bot_id="b", symbol="AAPL", strategy="S", side=side,
                is_exit=False, exec_algo=algo, order_id=None, signal_id=None,
                decision_price=100.0, arrival={"arrival_price": 100.0},
                requested_qty=1, filled_qty=1,
                avg_fill_price=100.0 + (is_bps / 100.0) * (1 if side == "BUY" else -1),
            )
        summary = execution_tca.execution_quality_summary()
        assert len(summary) == 2
        by_algo = {(r["exec_algo"], r["side"]): r for r in summary}
        assert by_algo[("single", "BUY")]["n"] == 2
        assert by_algo[("single", "BUY")]["avg_is_bps"] == pytest.approx(15.0, abs=0.5)
        assert by_algo[("vwap", "SELL")]["n"] == 1
