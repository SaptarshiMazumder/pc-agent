"""TTL cache for normalized search results (moved verbatim from web_search.py).

Module-private process cache keyed by ``query|count|freshness``. Values are now
``list[SearchResult]`` (previously ``list[dict]``); TTL/eviction logic unchanged.
"""

from __future__ import annotations

import time

from agent_runtime.application.interfaces.search import SearchResult

CACHE_TTL_SEC = 900
CACHE_MAX = 100

_CACHE: dict[str, tuple[float, list[SearchResult]]] = {}


def cache_get(key: str) -> list[SearchResult] | None:
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    _CACHE.pop(key, None)
    return None


def cache_put(key: str, value: list[SearchResult]) -> None:
    if len(_CACHE) >= CACHE_MAX:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
    _CACHE[key] = (time.time() + CACHE_TTL_SEC, value)
