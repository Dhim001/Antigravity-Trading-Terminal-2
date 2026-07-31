"""MEMORY_CENTRIC_REVIEW #29/#30 — retention pruning for the reject-telemetry
log (with daily rollup so long-term stats survive) and for ml_train_runs."""

import os
import sqlite3
import tempfile

import pytest

from app.services.bots import ml_train_runs, reject_telemetry


@pytest.fixture
def tmp_reject_db(monkeypatch):
    """Point reject_telemetry at a fresh sqlite with log + rollup tables."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE bot_signal_reject_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT NOT NULL,
            symbol TEXT,
            strategy TEXT,
            signal_kind TEXT,
            reason_bucket TEXT NOT NULL,
            reason_detail TEXT,
            confidence REAL,
            bar_time INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE bot_signal_reject_rollup (
            day TEXT NOT NULL,
            bot_id TEXT NOT NULL,
            reason_bucket TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, bot_id, reason_bucket)
        )
    """)
    conn.commit()
    conn.close()

    import contextlib

    @contextlib.contextmanager
    def fake_session():
        c = sqlite3.connect(path)
        try:
            yield c
            c.commit()
        finally:
            c.close()

    monkeypatch.setattr(reject_telemetry, "db_session", fake_session)
    monkeypatch.setattr(reject_telemetry, "is_postgres", lambda: False)
    yield path
    os.unlink(path)


def _insert_reject(path, bot_id, bucket, created_at):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO bot_signal_reject_log
            (bot_id, symbol, strategy, signal_kind, reason_bucket,
             reason_detail, confidence, bar_time, created_at)
        VALUES (?, 'AAPL', 'SMA_CROSS', 'entry', ?, NULL, NULL, NULL, ?)
        """,
        (bot_id, bucket, created_at),
    )
    conn.commit()
    conn.close()


def _count(path, table):
    conn = sqlite3.connect(path)
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return n


def test_prune_rolls_up_then_deletes_old_rows(tmp_reject_db):
    old = "2026-07-01T10:00:00.000000Z"  # ~27 days old
    new = reject_telemetry._now_iso()
    _insert_reject(tmp_reject_db, "bot-1", "none", old)
    _insert_reject(tmp_reject_db, "bot-1", "none", old)
    _insert_reject(tmp_reject_db, "bot-1", "htf_gate", old)
    _insert_reject(tmp_reject_db, "bot-1", "none", new)

    deleted = reject_telemetry.prune_reject_log(7)
    assert deleted == 3
    assert _count(tmp_reject_db, "bot_signal_reject_log") == 1

    # Stats survive in the rollup: bot-1 saw 2×none + 1×htf_gate on 2026-07-01.
    rollup = reject_telemetry.reject_rollup()
    assert rollup["bot-1"]["none"] == 2
    assert rollup["bot-1"]["htf_gate"] == 1


def test_prune_rollup_accumulates_across_passes(tmp_reject_db):
    old = "2026-07-01T10:00:00.000000Z"
    _insert_reject(tmp_reject_db, "bot-1", "none", old)
    assert reject_telemetry.prune_reject_log(7) == 1
    _insert_reject(tmp_reject_db, "bot-1", "none", old)
    assert reject_telemetry.prune_reject_log(7) == 1
    # Two separate passes summed into one rollup row.
    assert reject_telemetry.reject_rollup()["bot-1"]["none"] == 2


def test_prune_row_cap_keeps_newest(tmp_reject_db):
    now = reject_telemetry._now_iso()
    for i in range(6):
        _insert_reject(tmp_reject_db, f"bot-{i}", "none", now)
    deleted = reject_telemetry.prune_reject_log(0, max_rows=2)
    assert deleted == 4
    assert _count(tmp_reject_db, "bot_signal_reject_log") == 2
    # Newest two rows survive (highest autoincrement ids → bot-4, bot-5).
    conn = sqlite3.connect(tmp_reject_db)
    bots = {r[0] for r in conn.execute("SELECT bot_id FROM bot_signal_reject_log")}
    conn.close()
    assert bots == {"bot-4", "bot-5"}
    # …and the four evicted rows landed in the rollup.
    rollup = reject_telemetry.reject_rollup()
    assert sum(rollup[b]["none"] for b in ("bot-0", "bot-1", "bot-2", "bot-3")) == 4


# ── ml_train_runs retention (#30) ──────────────────────────────────────────


@pytest.fixture
def tmp_runs_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE ml_train_runs (
            id TEXT PRIMARY KEY,
            kind TEXT,
            strategy TEXT,
            symbol TEXT,
            started_at TEXT,
            finished_at TEXT,
            duration_ms INTEGER,
            ok INTEGER,
            error TEXT,
            metrics_json TEXT,
            config_hash TEXT,
            version_id TEXT,
            job_id TEXT,
            created_at TEXT,
            timeframe TEXT
        )
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(ml_train_runs, "get_connection", lambda: sqlite3.connect(path))
    yield path
    os.unlink(path)


def _insert_run(path, run_id, created_at):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO ml_train_runs (id, kind, strategy, symbol, ok, created_at) "
        "VALUES (?, 'train', 'LSTM_DIRECTION', 'AAPL', 1, ?)",
        (run_id, created_at),
    )
    conn.commit()
    conn.close()


def test_prune_ml_train_runs_deletes_old_rows(tmp_runs_db):
    _insert_run(tmp_runs_db, "old-1", "2026-06-01T00:00:00.000000Z")
    _insert_run(tmp_runs_db, "old-2", "2026-06-15T00:00:00.000000Z")
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _insert_run(tmp_runs_db, "new-1", now)

    deleted = ml_train_runs.prune_ml_train_runs(30)
    assert deleted == 2
    assert _count(tmp_runs_db, "ml_train_runs") == 1


def test_prune_ml_train_runs_zero_days_is_noop(tmp_runs_db):
    _insert_run(tmp_runs_db, "old-1", "2026-06-01T00:00:00.000000Z")
    assert ml_train_runs.prune_ml_train_runs(0) == 0
    assert _count(tmp_runs_db, "ml_train_runs") == 1
