"""Provenance helpers for backtest reproducibility manifests."""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def repo_git_revision() -> str | None:
    """Best-effort short git SHA for reproducibility bundles."""
    try:
        root = Path(__file__).resolve().parents[3]
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode == 0:
            rev = (proc.stdout or "").strip()
            return rev or None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None
