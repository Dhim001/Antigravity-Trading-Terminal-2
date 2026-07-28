"""Unit tests for reject_telemetry (silent-NONE telemetry)."""

import os
import sqlite3
import tempfile

import pytest

from app.services.bots import reject_telemetry


@pytest.fixture
def tmp_db(monkeypatch):
    """Point db_session at a fresh on-disk sqlite with the reject-log table."""
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


# ── record_reject ──────────────────────────────────────────────────────────


def test_record_reject_inserts_row(tmp_db):
    ok = reject_telemetry.record_reject(
        bot_id="bot-1", reason_bucket="none", symbol="AAPL",
        strategy="ML_SIGNAL_BOOST", signal_kind="entry",
        reason_detail="no edge", confidence=0.42, bar_time=1700000000,
    )
    assert ok is True
    counts = reject_telemetry.reject_counts()
    assert counts.get("none") == 1


def test_record_reject_no_bot_id_returns_false(tmp_db):
    assert reject_telemetry.record_reject(bot_id="", reason_bucket="none") is False


def test_record_reject_normalizes_unknown_bucket(tmp_db):
    reject_telemetry.record_reject(bot_id="b", reason_bucket="WAT")
    reject_telemetry.record_reject(bot_id="b", reason_bucket=None)
    counts = reject_telemetry.reject_counts()
    assert counts.get("other") == 2


def test_record_reject_never_raises_on_db_failure(monkeypatch):
    import contextlib

    @contextlib.contextmanager
    def bad_session():
        raise RuntimeError("no db")
        yield  # noqa

    monkeypatch.setattr(reject_telemetry, "db_session", bad_session)
    assert reject_telemetry.record_reject(bot_id="b", reason_bucket="none") is False


# ── reject_counts ───────────────────────────────────────────────────────────


def test_reject_counts_filters_by_bot(tmp_db):
    reject_telemetry.record_reject(bot_id="b1", reason_bucket="none")
    reject_telemetry.record_reject(bot_id="b1", reason_bucket="htf_gate")
    reject_telemetry.record_reject(bot_id="b2", reason_bucket="none")
    assert reject_telemetry.reject_counts(bot_id="b1") == {"none": 1, "htf_gate": 1}
    assert reject_telemetry.reject_counts(bot_id="b2") == {"none": 1}
    assert reject_telemetry.reject_counts()["none"] == 2


def test_reject_counts_handles_db_failure(monkeypatch):
    import contextlib

    @contextlib.contextmanager
    def bad_session():
        raise RuntimeError("no db")
        yield  # noqa

    monkeypatch.setattr(reject_telemetry, "db_session", bad_session)
    assert reject_telemetry.reject_counts() == {}


# ── reject_breakdown_by_bot ────────────────────────────────────────────────


def test_reject_breakdown_by_bot_groups_correctly(tmp_db):
    reject_telemetry.record_reject(bot_id="b1", reason_bucket="none", symbol="AAPL")
    reject_telemetry.record_reject(bot_id="b1", reason_bucket="none", symbol="MSFT")
    reject_telemetry.record_reject(bot_id="b2", reason_bucket="htf_gate")
    breakdown = reject_telemetry.reject_breakdown_by_bot()
    assert breakdown["b1"] == {"none": 2}
    assert breakdown["b2"] == {"htf_gate": 1}


# ── clear_reject_log ────────────────────────────────────────────────────────


def test_clear_reject_log_removes_rows(tmp_db):
    reject_telemetry.record_reject(bot_id="b", reason_bucket="none")
    reject_telemetry.record_reject(bot_id="b", reason_bucket="htf_gate")
    deleted = reject_telemetry.clear_reject_log()
    assert deleted >= 2
    assert reject_telemetry.reject_counts() == {}


# ── classify_reject ─────────────────────────────────────────────────────────


def test_classify_reject_explicit_gate_wins():
    assert reject_telemetry.classify_reject({}, gate="htf_gate") == "htf_gate"
    assert reject_telemetry.classify_reject({}, gate="UNKNOWN") == "other"


def test_classify_reject_infers_from_reason():
    assert reject_telemetry.classify_reject({"reject_reason": "low confidence"}) == "low_confidence"
    assert reject_telemetry.classify_reject({"reject_reason": "filter: no trend"}) == "filter"
    assert reject_telemetry.classify_reject({"reject_reason": "meta-label rejected"}) == "meta_label"
    assert reject_telemetry.classify_reject({"reject_reason": "conformal ambiguous"}) == "conformal"
    assert reject_telemetry.classify_reject({"reject_reason": "regime bear"}) == "regime_gate"
    assert reject_telemetry.classify_reject({"reject_reason": "stacking margin"}) == "stacking"
    assert reject_telemetry.classify_reject({"reject_reason": "llm firewall veto"}) == "llm_firewall"
    assert reject_telemetry.classify_reject({"reject_reason": "htf bias disagrees"}) == "htf_gate"
    assert reject_telemetry.classify_reject({"reject_reason": "something weird"}) == "other"


def test_classify_reject_no_reason_returns_none():
    assert reject_telemetry.classify_reject({}) == "none"
    assert reject_telemetry.classify_reject(None) == "none"


# ── KNOWN_BUCKETS sanity ───────────────────────────────────────────────────


def test_known_buckets_complete():
    assert "none" in reject_telemetry.KNOWN_BUCKETS
    assert "htf_gate" in reject_telemetry.KNOWN_BUCKETS
    assert "other" in reject_telemetry.KNOWN_BUCKETS
