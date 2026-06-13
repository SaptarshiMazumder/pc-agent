"""web_search tool: Brave Search API when a key is configured, otherwise
DuckDuckGo (keyless) via the ddgs package. Results normalized to
[{title, url, snippet, age}] and TTL-cached."""

from __future__ import annotations

import asyncio
import time

import httpx

from . import Tool, ToolResult

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
CACHE_TTL_SEC = 900
CACHE_MAX = 100

_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _cache_get(key: str) -> list[dict] | None:
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    _CACHE.pop(key, None)
    return None


def _cache_put(key: str, value: list[dict]) -> None:
    if len(_CACHE) >= CACHE_MAX:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
    _CACHE[key] = (time.time() + CACHE_TTL_SEC, value)


async def search_brave(query: str, count: int, api_key: str, freshness: str | None) -> list[dict]:
    params: dict = {"q": query, "count": count}
    freshness_map = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}
    if freshness in freshness_map:
        params["freshness"] = freshness_map[freshness]
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            BRAVE_ENDPOINT,
            params=params,
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    results = (data.get("web") or {}).get("results") or []
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("description", ""),
            "age": r.get("age", ""),
        }
        for r in results
    ]


def _search_ddg_sync(query: str, count: int) -> list[dict]:
    from ddgs import DDGS

    with DDGS() as ddgs:
        raw = list(ddgs.text(query, max_results=count))
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("href") or r.get("url", ""),
            "snippet": r.get("body", ""),
            "age": "",
        }
        for r in raw
    ]


def format_results(results: list[dict], provider: str) -> str:
    if not results:
        return f"No results (provider: {provider})."
    lines = []
    for i, r in enumerate(results, 1):
        age = f"  ({r['age']})" if r.get("age") else ""
        lines.append(f"{i}. {r['title']}{age}\n   {r['url']}\n   {r['snippet']}")
    return f"Search results (provider: {provider}):\n\n" + "\n\n".join(lines)


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web for current information. Returns titles, URLs, and snippets."
    label = "Web Search"
    parameters = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "count": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Result count (default 5)."},
            "freshness": {
                "type": "string",
                "enum": ["day", "week", "month", "year"],
                "description": "Restrict results by recency.",
            },
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None):
        query = params["query"]
        count = params.get("count", 5)
        freshness = params.get("freshness")

        cache_key = f"{query}|{count}|{freshness}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return ToolResult.text(format_results(cached, "cache"))

        try:
            if self.config.brave_api_key:
                provider = "brave"
                results = await search_brave(query, count, self.config.brave_api_key, freshness)
            else:
                provider = "duckduckgo"
                results = await asyncio.to_thread(_search_ddg_sync, query, count)
        except Exception as e:
            return ToolResult.text(f"web_search failed: {type(e).__name__}: {e}", is_error=True)

        _cache_put(cache_key, results)
        return ToolResult.text(format_results(results, provider), details=results)
