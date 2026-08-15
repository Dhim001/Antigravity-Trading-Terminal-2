"""Global pytest guards — never touch live profile SQLite databases."""

from __future__ import annotations

import os
import tempfile

import pytest

# Pin a temp DB *before* any app import in collection/run. Individual tests may
# still override SQLITE_DB_PATH; profile env must not rewrite it to trading-*.db.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="tt-pytest-")
_TEST_DB_PATH = os.path.join(_TEST_DB_DIR, "pytest.db")

# Clear profile so alpaca/ib/sim env files cannot redirect onto live DBs when
# the developer shell still has TERMINAL_PROFILE set from start-desktop.ps1.
os.environ.pop("TERMINAL_PROFILE", None)
os.environ["DATABASE_URL"] = ""
os.environ["SQLITE_DB_PATH"] = _TEST_DB_PATH
os.environ.setdefault("TERMINAL_MODE", "SIMULATED")

_LIVE_DB_NAMES = {
    "trading-alpaca.db",
    "trading-ib.db",
    "trading-massive.db",
    "trading-sim.db",
    "trading.db",
}


@pytest.fixture(autouse=True)
def _refuse_live_sqlite_db():
    """Fail fast if a test somehow bound to a live profile database file."""
    try:
        import app.config as app_config
        import app.db.connection as db_conn
    except Exception:
        yield
        return

    path = str(getattr(db_conn, "DB_PATH", "") or getattr(app_config, "DB_PATH", "") or "")
    base = os.path.basename(path.replace("\\", "/")).lower()
    if base in _LIVE_DB_NAMES:
        pytest.fail(
            f"Refusing to run tests against live DB {path!r}. "
            "Unset TERMINAL_PROFILE and pin SQLITE_DB_PATH to a temp file."
        )
    yield
