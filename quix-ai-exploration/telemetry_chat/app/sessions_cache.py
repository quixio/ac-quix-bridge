"""In-process TTL cache for the QuixLake partition walk.

First /api/plot call pays the ~300ms walk; subsequent calls within TTL reuse
the cached list. A "New chat" in the UI does NOT invalidate this cache —
sessions themselves don't change just because a chat restarted, and we
prefer the stale-but-consistent reading over a surprise 300ms stall. The
TTL is the only freshness mechanism; adjust via env `SESSIONS_CACHE_TTL`.
"""

from __future__ import annotations

import asyncio
import time

from . import config
from .partitions import walk_partition_tree

_lock = asyncio.Lock()
_fetched_at: float = 0.0
_sessions: list[dict] = []


async def get_sessions(force: bool = False) -> list[dict]:
    """Return the cached sessions list, refreshing if the TTL has elapsed.

    Thread-safe: concurrent callers on a cold cache all await the same fetch.
    """
    global _fetched_at, _sessions
    now = time.monotonic()
    if not force and now - _fetched_at < config.SESSIONS_CACHE_TTL:
        return _sessions

    async with _lock:
        # Re-check under lock — another waiter may have just refreshed it.
        now = time.monotonic()
        if not force and now - _fetched_at < config.SESSIONS_CACHE_TTL:
            return _sessions
        _sessions = await walk_partition_tree()
        _fetched_at = time.monotonic()
        return _sessions
