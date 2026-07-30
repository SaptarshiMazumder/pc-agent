"""BraveProvider — Brave Search API adapter (logic moved verbatim from
web_search.search_brave; now returns SearchResult and lives behind the port)."""

from __future__ import annotations

import httpx

from agent_runtime.application.interfaces.search import SearchResult

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_FRESHNESS_MAP = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}


class BraveProvider:
    name = "brave"

    def __init__(self, api_key: str):
        self._api_key = api_key

    def available(self) -> bool:
        return bool(self._api_key)

    async def search(self, query: str, count: int, freshness: str | None) -> list[SearchResult]:
        params: dict = {"q": query, "count": count}
        if freshness in _FRESHNESS_MAP:
            params["freshness"] = _FRESHNESS_MAP[freshness]
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                BRAVE_ENDPOINT,
                params=params,
                headers={"X-Subscription-Token": self._api_key, "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        results = (data.get("web") or {}).get("results") or []
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", ""),
                age=r.get("age", ""),
            )
            for r in results
        ]
