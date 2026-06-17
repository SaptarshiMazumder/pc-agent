"""web_search tool: a thin DISPATCHER over an ordered SearchProvider chain.

The tool knows nothing about any backend. It runs the injected providers in order
(skipping unavailable ones), returns the first non-empty result set, and caches it.
Backends (Gemini grounding / Brave / DuckDuckGo / ...) live in `search/providers/`
and are selected + ordered by config in `search/factory.py`.
"""

from __future__ import annotations

from agentd.application.interfaces.search import SearchProvider
from agentd.infrastructure.tools.search import cache_get, cache_put, format_results

from . import Tool, ToolResult


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web for current information. Returns titles, URLs, and snippets."
    label = "Web Search"
    parameters = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "count": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Result count (default 10)."},
            "freshness": {
                "type": "string",
                "enum": ["day", "week", "month", "year"],
                "description": "Restrict results by recency.",
            },
        },
    }

    def __init__(self, config, providers: list[SearchProvider] | None = None):
        self.config = config
        self.providers = providers if providers is not None else []

    async def execute(self, tool_call_id, params, abort, on_update=None):
        query = params["query"]
        count = params.get("count", 10)
        freshness = params.get("freshness")

        cache_key = f"{query}|{count}|{freshness}"
        cached = cache_get(cache_key)
        if cached is not None:
            return ToolResult.text(format_results(cached, "cache"), details=cached)

        last_error: str | None = None
        for provider in self.providers:
            if abort.is_set():
                return ToolResult.text("web_search aborted.", is_error=True)
            if not provider.available():
                continue
            try:
                results = await provider.search(query, count, freshness)
            except Exception as e:  # provider failed -> try the next one
                last_error = f"{provider.name}: {type(e).__name__}: {e}"
                continue
            if results:  # first non-empty wins
                cache_put(cache_key, results)
                return ToolResult.text(format_results(results, provider.name), details=results)
            # empty -> fall through to the next provider

        if last_error is not None:
            return ToolResult.text(f"web_search failed: {last_error}", is_error=True)
        return ToolResult.text("web_search: no results from any provider.")
