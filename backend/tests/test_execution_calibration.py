"""EXECUTION_RISK_INTELLIGENCE_PLAN Phase 2 — backtest cost calibration from
measured live execution (champion-challenger suggestions + operator apply),
plus the dashboard aggregates and HTTP handlers."""

import asyncio
import os
import sqlite3
import tempfile
import types

import pytest

from app import config
from app.api.http import app as http_app
from app.services.bots import execution_calibration, execution_tca


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_phase2_db(monkeypatch):
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
        CREATE TABLE execution_cost_calibration (
            symbol TEXT PRIMARY KEY,
            sample_size INTEGER,
            measured_exec_bps REAL,
            measured_delay_bps REAL,
            suggested_slippage_bps REAL,
            suggested_latency_bps REAL,
            computed_at TEXT,
            applied_at TEXT
        )
    """)
    conn.commit()
    conn.close()

    def fake_get_connection():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(execution_tca, "get_connection", fake_get_connection)
    monkeypatch.setattr(execution_calibration, "get_connection", fake_get_connection)
    monkeypatch.setattr(config, "EXEC_QUALITY_LOG_ENABLED", True)
    yield path
    os.unlink(path)


def _insert_fill(path, *, symbol="AAPL", side="BUY", algo="single", strategy="TREND",
                 bot_id="b1", is_bps=10.0, delay_bps=2.0, spread_bps=3.0,
                 impact_bps=5.0, opp_bps=None, filled=1.0, created_at=None):
    conn = sqlite3.connect(path)
    if created_at:
        conn.execute(
            """INSERT INTO execution_quality_log
               (bot_id, symbol, strategy, side, exec_algo, order_id,
                decision_price, arrival_price, avg_fill_price, requested_qty, filled_qty,
                is_bps, delay_bps, spread_bps, impact_bps, opp_bps, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (bot_id, symbol, strategy, side, algo, f"o-{symbol}-{is_bps}-{created_at}",
             100.0, 100.0, 101.0, filled, filled,
             is_bps, delay_bps, spread_bps, impact_bps, opp_bps, created_at),
        )
    else:
        conn.execute(
            """INSERT INTO execution_quality_log
               (bot_id, symbol, strategy, side, exec_algo, order_id,
                decision_price, arrival_price, avg_fill_price, requested_qty, filled_qty,
                is_bps, delay_bps, spread_bps, impact_bps, opp_bps)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (bot_id, symbol, strategy, side, algo, f"o-{symbol}-{is_bps}-{filled}",
             100.0, 100.0, 101.0, filled, filled,
             is_bps, delay_bps, spread_bps, impact_bps, opp_bps),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Dashboard aggregates
# ---------------------------------------------------------------------------

class TestDashboard:
    def test_kpis_and_breakdowns(self, tmp_phase2_db):
        for i in range(3):
            _insert_fill(tmp_phase2_db, is_bps=10.0 + i)
        _insert_fill(tmp_phase2_db, symbol="MSFT", algo="vwap", is_bps=-4.0)

        dash = execution_tca.execution_quality_dashboard()
        assert dash["kpis"]["n"] == 4
        assert len(dash["by_symbol"]) == 2
        assert {r["exec_algo"] for r in dash["by_algo"]} == {"single", "vwap"}
        assert dash["by_strategy"][0]["strategy"] == "TREND"

    def test_filters_narrow_results(self, tmp_phase2_db):
        _insert_fill(tmp_phase2_db, symbol="AAPL", bot_id="b1")
        _insert_fill(tmp_phase2_db, symbol="MSFT", bot_id="b2")

        dash = execution_tca.execution_quality_dashboard(bot_id="b2")
        assert dash["kpis"]["n"] == 1
        assert dash["by_symbol"][0]["symbol"] == "MSFT"

        dash = execution_tca.execution_quality_dashboard(symbol="AAPL")
        assert dash["kpis"]["n"] == 1

    def test_trend_groups_by_day_and_orders_asc(self, tmp_phase2_db):
        _insert_fill(tmp_phase2_db, is_bps=10.0, created_at="2026-07-27 10:00:00")
        _insert_fill(tmp_phase2_db, is_bps=20.0, created_at="2026-07-27 11:00:00")
        _insert_fill(tmp_phase2_db, is_bps=-6.0, created_at="2026-07-28 09:00:00")

        dash = execution_tca.execution_quality_dashboard()
        assert [t["day"] for t in dash["trend"]] == ["2026-07-27", "2026-07-28"]
        assert dash["trend"][0]["avg_is_bps"] == pytest.approx(15.0)
        assert dash["trend"][0]["n"] == 2

    def test_worst_fills_ordered_by_is_desc_nulls_last(self, tmp_phase2_db):
        _insert_fill(tmp_phase2_db, is_bps=5.0)
        _insert_fill(tmp_phase2_db, is_bps=42.0)
        _insert_fill(tmp_phase2_db, is_bps=None, filled=0.5)

        dash = execution_tca.execution_quality_dashboard(worst_limit=3)
        is_values = [f["is_bps"] for f in dash["worst_fills"]]
        assert is_values[0] == pytest.approx(42.0)
        assert is_values[1] == pytest.approx(5.0)
        assert is_values[2] is None


# ---------------------------------------------------------------------------
# Cost calibration
# ---------------------------------------------------------------------------

class TestComputeSuggestions:
    def test_suggestion_from_measured_exec_cost(self, tmp_phase2_db, monkeypatch):
        monkeypatch.setattr(config, "EXEC_CAL_MIN_SAMPLES", 3)
        monkeypatch.setattr(config, "EXEC_CAL_SAFETY_FACTOR", 1.25)
        # spread 4 + impact 8 = 12 bps measured exec; ×1.25 = 15 bps suggested
        for _ in range(3):
            _insert_fill(tmp_phase2_db, spread_bps=4.0, impact_bps=8.0, delay_bps=6.0)

        out = execution_calibration.compute_cost_suggestions()
        assert len(out) == 1
        s = out[0]
        assert s["symbol"] == "AAPL"
        assert s["sample_size"] == 3
        assert s["measured_exec_bps"] == pytest.approx(12.0)
        assert s["suggested_slippage_bps"] == pytest.approx(15.0)
        assert s["suggested_latency_bps"] == pytest.approx(6.0)

    def test_min_sample_gate_marks_insufficient_and_skips_persist(
        self, tmp_phase2_db, monkeypatch
    ):
        monkeypatch.setattr(config, "EXEC_CAL_MIN_SAMPLES", 10)
        for _ in range(3):
            _insert_fill(tmp_phase2_db)

        out = execution_calibration.compute_cost_suggestions()
        assert out[0]["insufficient_data"] is True
        assert "suggested_slippage_bps" not in out[0]
        assert execution_calibration.list_cost_suggestions() == []

    def test_clamp_to_max_bps(self, tmp_phase2_db, monkeypatch):
        monkeypatch.setattr(config, "EXEC_CAL_MIN_SAMPLES", 2)
        monkeypatch.setattr(config, "EXEC_CAL_SAFETY_FACTOR", 1.25)
        monkeypatch.setattr(config, "EXEC_CAL_MAX_BPS", 200.0)
        for _ in range(2):
            _insert_fill(tmp_phase2_db, spread_bps=500.0, impact_bps=500.0)

        out = execution_calibration.compute_cost_suggestions()
        assert out[0]["suggested_slippage_bps"] == pytest.approx(200.0)

    def test_negative_delay_floors_to_zero(self, tmp_phase2_db, monkeypatch):
        monkeypatch.setattr(config, "EXEC_CAL_MIN_SAMPLES", 2)
        for _ in range(2):
            _insert_fill(tmp_phase2_db, delay_bps=-8.0)

        out = execution_calibration.compute_cost_suggestions()
        assert out[0]["suggested_latency_bps"] == pytest.approx(0.0)

    def test_zero_fills_excluded(self, tmp_phase2_db, monkeypatch):
        monkeypatch.setattr(config, "EXEC_CAL_MIN_SAMPLES", 1)
        _insert_fill(tmp_phase2_db, filled=0.0, is_bps=None, spread_bps=None, impact_bps=None)
        out = execution_calibration.compute_cost_suggestions()
        assert out == []

    def test_db_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            execution_calibration, "get_connection",
            lambda: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        assert execution_calibration.compute_cost_suggestions() == []


class TestApplySuggestion:
    def test_apply_returns_patch_and_stamps(self, tmp_phase2_db, monkeypatch):
        monkeypatch.setattr(config, "EXEC_CAL_MIN_SAMPLES", 2)
        for _ in range(2):
            _insert_fill(tmp_phase2_db, spread_bps=4.0, impact_bps=8.0)
        execution_calibration.compute_cost_suggestions()

        patch = execution_calibration.apply_cost_suggestion("AAPL")
        assert patch is not None
        assert patch["slippage_bps"] == pytest.approx(15.0)
        assert patch["latency_slippage_bps"] == pytest.approx(2.0)
        assert patch["applied_at"]

        rows = execution_calibration.list_cost_suggestions()
        assert rows[0]["applied"] is True

    def test_applied_flag_survives_recompute(self, tmp_phase2_db, monkeypatch):
        monkeypatch.setattr(config, "EXEC_CAL_MIN_SAMPLES", 2)
        for _ in range(2):
            _insert_fill(tmp_phase2_db)
        execution_calibration.compute_cost_suggestions()
        execution_calibration.apply_cost_suggestion("AAPL")

        # Recompute (nightly refresh) must preserve the approval stamp.
        _insert_fill(tmp_phase2_db)
        execution_calibration.compute_cost_suggestions()
        rows = execution_calibration.list_cost_suggestions()
        assert rows[0]["applied"] is True

    def test_apply_unknown_symbol_returns_none(self, tmp_phase2_db):
        assert execution_calibration.apply_cost_suggestion("NOPE") is None
        assert execution_calibration.apply_cost_suggestion(None) is None


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------

def _query_request(params: dict[str, str]):
    return types.SimpleNamespace(
        query_params=params,
        path_params={},
        app=types.SimpleNamespace(state=types.SimpleNamespace()),
    )


class TestHandlers:
    def test_quality_handler_returns_dashboard(self, tmp_phase2_db):
        _insert_fill(tmp_phase2_db)
        req = _query_request({"hours": "0"})
        resp = asyncio.run(http_app.get_execution_quality_handler(req))
        assert resp.status_code == 200
        import json as _json

        body = _json.loads(resp.body)
        assert body["ok"] is True
        assert body["execution"]["kpis"]["n"] == 1

    def test_cost_suggestions_handler_merges_insufficient(
        self, tmp_phase2_db, monkeypatch
    ):
        monkeypatch.setattr(config, "EXEC_CAL_MIN_SAMPLES", 5)
        _insert_fill(tmp_phase2_db, symbol="AAPL")  # 1 fill → insufficient
        for _ in range(5):
            _insert_fill(tmp_phase2_db, symbol="MSFT", algo="vwap")

        req = _query_request({"refresh": "1"})
        resp = asyncio.run(http_app.get_execution_cost_suggestions_handler(req))
        import json as _json

        body = _json.loads(resp.body)
        assert body["ok"] is True
        by_symbol = {s["symbol"]: s for s in body["suggestions"]}
        assert by_symbol["AAPL"]["insufficient_data"] is True
        assert "suggested_slippage_bps" in by_symbol["MSFT"]

    def test_apply_handler_404_on_unknown(self, tmp_phase2_db):
        async def _json():
            return {"symbol": "NOPE"}

        req = types.SimpleNamespace(json=_json)
        resp = asyncio.run(http_app.apply_execution_cost_suggestion_handler(req))
        assert resp.status_code == 404

    def test_apply_handler_returns_patch(self, tmp_phase2_db, monkeypatch):
        monkeypatch.setattr(config, "EXEC_CAL_MIN_SAMPLES", 2)
        for _ in range(2):
            _insert_fill(tmp_phase2_db)
        execution_calibration.compute_cost_suggestions()

        async def _json():
            return {"symbol": "AAPL"}

        req = types.SimpleNamespace(json=_json)
        resp = asyncio.run(http_app.apply_execution_cost_suggestion_handler(req))
        import json as _json_lib

        body = _json_lib.loads(resp.body)
        assert resp.status_code == 200
        assert body["ok"] is True
        assert body["applied"]["symbol"] == "AAPL"
        assert body["applied"]["slippage_bps"] > 0

    def test_apply_handler_tolerates_empty_body(self, tmp_phase2_db):
        async def _json():
            raise ValueError("no body")

        req = types.SimpleNamespace(json=_json)
        resp = asyncio.run(http_app.apply_execution_cost_suggestion_handler(req))
        assert resp.status_code == 404
