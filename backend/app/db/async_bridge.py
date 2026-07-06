"""Run blocking SQLite work off the asyncio loop on a single dedicated thread.

Concurrent asyncio.to_thread() DB calls from the default pool can saturate
SQLite writers and wedge the server; serializing DB I/O avoids lock storms.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")

_executor: ThreadPoolExecutor | None = None


def _db_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite-db")
    return _executor


async def run_db(fn: Callable[..., T], /, *args, **kwargs) -> T:
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(_db_executor(), lambda: fn(*args, **kwargs))
    return await loop.run_in_executor(_db_executor(), fn, *args)
