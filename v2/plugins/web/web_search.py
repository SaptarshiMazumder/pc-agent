"""web_search tool: a thin DISPATCHER over an ordered SearchProvider chain.

The tool knows nothing about any backend. It runs the injected providers in order
(skipping unavailable ones), returns the first non-empty result set, and caches it.
Backends (Gemini grounding / Brave / DuckDuckGo / ...) live in `search/providers/`
and are selected + ordered by config in `search/factory.py`.
"""

from __future__ import annotations

from search import cache_get, cache_put, format_results
from search.factory import search_provider_names

from agentd.application.interfaces.search import SearchProvider
from agentd.application.interfaces.tool import Tool, ToolResult


class WebSearchTool(Tool):
    name = "web_search"
    default_timeout_sec = 30.0
    default_retryable = True
    default_max_retries = 2
    description = (
        "Search the web for current information. Returns titles, URLs, and snippets.\n"
        "Write the query as a natural-language phrase describing what you want. Do NOT use "
        "search-engine operators like site:, quotes, OR, or intitle: — the search "
        "backend is semantic and ignores/mishandles them, giving worse results.\n"
        "Returns only PUBLIC, anonymous web content. It cannot reach pages behind a "
        "login, your own accounts, or sites that block crawlers (most social networks). "
        "For anything that needs a signed-in session, use the browser tool."
    )
    label = "Web Search"
    # provider = an ORDERED CHAIN of search backends, self-described so a client shows a picker (not a
    # text box). The names ARE the factory registry keys, so they can't drift from what resolves. An
    # empty chain (no picks) => the "auto" default order.
    provider_options = list(search_provider_names())
    provider_chain = True
    parameters = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "Result count (default 10).",
            },
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
