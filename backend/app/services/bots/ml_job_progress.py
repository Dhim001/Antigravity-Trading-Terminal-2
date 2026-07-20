"""Progress-file helpers for ML train/validate workers (cross-process).

Parent polls ``progress_path``; workers overwrite JSON. Cooperative cancel uses
``<progress_path>.cancel`` (empty flag file).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Any

logger = logging.getLogger(__name__)


def make_progress_path(job_id: str) -> str:
    """Create an empty progress JSON file; return its path."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(job_id))[:40]
    fd, path = tempfile.mkstemp(prefix=f"mljob_{safe}_", suffix=".progress.json")
    try:
        os.write(fd, b'{"pct":0,"phase":"queued","detail":""}')
    finally:
        os.close(fd)
    return path


def cancel_flag_path(progress_path: str | None) -> str | None:
    if not progress_path:
        return None
    return f"{progress_path}.cancel"


def write_ml_progress(
    progress_path: str | None,
    *,
    pct: float | int,
    phase: str,
    detail: str = "",
) -> None:
    """Overwrite progress JSON (best-effort; never raises into trainers)."""
    if not progress_path:
        return
    payload = {
        "pct": max(0, min(100, int(pct))),
        "phase": str(phase or ""),
        "detail": str(detail or ""),
        "updated_at": time.time(),
    }
    try:
        tmp = f"{progress_path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, progress_path)
    except OSError as exc:
        logger.debug("ml progress write failed: %s", exc)


def read_ml_progress(progress_path: str | None) -> dict[str, Any] | None:
    if not progress_path or not os.path.isfile(progress_path):
        return None
    try:
        with open(progress_path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def ml_cancel_requested(progress_path: str | None) -> bool:
    flag = cancel_flag_path(progress_path)
    return bool(flag and os.path.isfile(flag))


def cancelled_train_result(symbol: str, strategy: str) -> dict:
    """Standard cancel payload for trainers (matches RL PPO / executor)."""
    return {
        "ok": False,
        "cancelled": True,
        "error": "cancelled",
        "symbol": symbol,
        "strategy": strategy,
    }


def request_ml_cancel_file(progress_path: str | None) -> None:
    flag = cancel_flag_path(progress_path)
    if not flag:
        return
    try:
        with open(flag, "w", encoding="utf-8") as fh:
            fh.write("1")
    except OSError as exc:
        logger.debug("ml cancel flag write failed: %s", exc)


def cleanup_ml_progress(progress_path: str | None) -> None:
    if not progress_path:
        return
    for path in (progress_path, f"{progress_path}.tmp", cancel_flag_path(progress_path)):
        if not path:
            continue
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def progress_path_from_config(config: dict | None) -> str | None:
    if not isinstance(config, dict):
        return None
    path = config.get("_progress_path") or config.get("progress_path")
    return str(path) if path else None
