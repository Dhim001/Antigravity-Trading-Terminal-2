"""TTL cache for expensive get_db_stats() aggregations."""

from __future__ import annotations

import copy
import time
from typing import Any

from app.config import DIAGNOSTICS_STATS_CACHE_SEC

_cache: dict[str, Any] | None = None
_cache_ts: float = 0.0
_cache_include_archive: bool | None = None


def get_db_stats_cached(
    *,
    force_refresh: bool = False,
    include_archive: bool = True,
) -> dict[str, Any]:
    """Return a deep copy of cached DB stats (refreshed every DIAGNOSTICS_STATS_CACHE_SEC)."""
    global _cache, _cache_ts, _cache_include_archive
    from app.database import get_db_stats

    now = time.monotonic()
    ttl = max(5.0, float(DIAGNOSTICS_STATS_CACHE_SEC))
    if (
        not force_refresh
        and _cache is not None
        and _cache_include_archive == include_archive
        and (now - _cache_ts) < ttl
    ):
        return copy.deepcopy(_cache)

    stats = get_db_stats(include_archive=include_archive)
    _cache = stats
    _cache_ts = now
    _cache_include_archive = include_archive
    return copy.deepcopy(stats)


def invalidate_db_stats_cache() -> None:
    global _cache, _cache_ts, _cache_include_archive
    _cache = None
    _cache_ts = 0.0
    _cache_include_archive = None
