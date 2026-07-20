"""Write-ahead log for market bar archive — recover rows when DB flush fails."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

WAL_DIR = Path(os.environ.get("ARCHIVE_WAL_DIR", os.path.join(BASE_DIR, "data", "archive_wal")))
WAL_FILE = WAL_DIR / "pending.jsonl"


def append_wal_rows(rows: list[dict]) -> None:
    if not rows:
        return
    WAL_DIR.mkdir(parents=True, exist_ok=True)
    with WAL_FILE.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    logger.warning("Archived %d bar(s) to WAL (%s)", len(rows), WAL_FILE)


def replay_wal(upsert_fn) -> int:
    """Replay pending WAL rows through upsert_fn; clear WAL on success."""
    if not WAL_FILE.exists():
        return 0

    rows: list[dict] = []
    try:
        with WAL_FILE.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
    except Exception as exc:
        logger.warning("Failed to read archive WAL: %s", exc)
        return 0

    if not rows:
        WAL_FILE.unlink(missing_ok=True)
        return 0

    try:
        written = upsert_fn(rows)
        WAL_FILE.unlink(missing_ok=True)
        logger.info("Replayed %d archived bar(s) from WAL", written)
        return written
    except Exception as exc:
        logger.warning("Archive WAL replay failed (%d rows retained): %s", len(rows), exc)
        return 0
