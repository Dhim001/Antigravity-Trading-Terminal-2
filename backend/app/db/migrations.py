"""Schema version tracking — baseline for future Alembic migrations."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from app.db.connection import db_session, is_postgres

logger = logging.getLogger(__name__)

BASELINE_REVISION = "001_baseline"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_schema_migrations_table() -> None:
    with db_session() as conn:
        cur = conn.cursor()
        if is_postgres():
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        else:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )


def get_applied_revisions() -> list[str]:
    ensure_schema_migrations_table()
    with db_session() as conn:
        cur = conn.cursor()
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        rows = cur.fetchall()
    out: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(str(row.get("version", "")))
        else:
            out.append(str(row[0]))
    return [v for v in out if v]


def record_baseline_if_needed(revision: str = BASELINE_REVISION) -> bool:
    """Record init_db baseline revision once. Returns True if newly applied."""
    ensure_schema_migrations_table()
    applied = get_applied_revisions()
    if revision in applied:
        return False

    with db_session() as conn:
        cur = conn.cursor()
        if is_postgres():
            cur.execute(
                """
                INSERT INTO schema_migrations (version, applied_at)
                VALUES (?, NOW())
                ON CONFLICT (version) DO NOTHING
                """,
                (revision,),
            )
        else:
            cur.execute(
                """
                INSERT OR IGNORE INTO schema_migrations (version, applied_at)
                VALUES (?, ?)
                """,
                (revision, _now_iso()),
            )
        inserted = cur.rowcount > 0

    if inserted:
        logger.info("Recorded schema migration baseline: %s", revision)
    return inserted


def run_alembic_upgrade_if_enabled() -> None:
    """Run `alembic upgrade head` when ALEMBIC_AUTO_UPGRADE=1 (Docker / CI)."""
    flag = os.environ.get("ALEMBIC_AUTO_UPGRADE", "").lower()
    if flag not in ("1", "true", "yes"):
        return

    backend_root = Path(__file__).resolve().parents[2]
    ini_path = backend_root / "alembic.ini"
    if not ini_path.is_file():
        logger.warning("ALEMBIC_AUTO_UPGRADE set but %s not found", ini_path)
        return

    try:
        from alembic.config import Config
        from alembic import command

        cfg = Config(str(ini_path))
        cfg.set_main_option("script_location", str(backend_root / "alembic"))
        command.upgrade(cfg, "head")
        logger.info("Alembic upgrade head completed")
    except Exception:
        logger.exception("Alembic auto-upgrade failed")
        raise
