"""DuckDuckGoProvider — keyless last-resort adapter (logic moved verbatim from
web_search._search_ddg_sync; the sync ddgs call is wrapped in a thread).

Note: DuckDuckGo (via ddgs) has no freshness param, so `freshness` is ignored —
this preserves the prior behavior exactly.
"""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.search import SearchResult


class DuckDuckGoProvider:
    name = "duckduckgo"

    def available(self) -> bool:
        try:
            import ddgs  # noqa: F401
        except ImportError:
            return False
        return True

    async def search(self, query: str, count: int, freshness: str | None) -> list[SearchResult]:
        return await asyncio.to_thread(self._sync, query, count)

    @staticmethod
    def _sync(query: str, count: int) -> list[SearchResult]:
        from ddgs import DDGS

        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=count))
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("href") or r.get("url", ""),
                snippet=r.get("body", ""),
            )
            for r in raw
        ]
