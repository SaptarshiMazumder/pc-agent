"""web_fetch tool: a thin DISPATCHER over a FetchProvider chain.

httpx first; if it fails or returns thin content (a JS shell), escalate to the
browser-render provider which executes the page's JS. The tool knows nothing
about httpx or Playwright — backends live in `fetch/providers/`.
"""

from __future__ import annotations

import time

from agentd.application.interfaces.fetch import FetchProvider, FetchResult
from fetch import DEFAULT_MAX_CHARS, MAX_CHARS_CAP, sanitize_url

from agentd.application.interfaces.tool import Tool, ToolResult

CACHE_TTL_SEC = 900
# Below this many characters a result is treated as "thin" — try the next
# provider (e.g. browser-render) and keep this as a fallback candidate.
MIN_USEFUL_CHARS = 200

_CACHE: dict[str, tuple[float, FetchResult]] = {}


def _cache_get(key: str) -> FetchResult | None:
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    _CACHE.pop(key, None)
    return None


def _cache_put(key: str, value: FetchResult) -> None:
    _CACHE[key] = (time.time() + CACHE_TTL_SEC, value)


def _render(result: FetchResult) -> str:
    header = f"{result.final_url} (status {result.status})"
    if result.title:
        header += f"\n# {result.title}"
    return f"{header}\n\n{result.text}"


class WebFetchTool(Tool):
    name = "web_fetch"
    default_timeout_sec = 120.0  # may escalate to browser-render
    default_retryable = True
    default_max_retries = 2
    description = (
        "Fetch a URL and return its readable content as markdown. Fetches the page "
        "ANONYMOUSLY (no login, no session). For pages behind a sign-in, your own "
        "accounts, interactive flows, or when a fetch comes back blocked/empty/"
        "login-walled, use the browser tool instead."
    )
    label = "Web Fetch"
    parameters = {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "description": "HTTP(S) URL to fetch."},
            "max_chars": {
                "type": "integer",
                "minimum": 100,
                "description": f"Max characters returned (default {DEFAULT_MAX_CHARS}).",
            },
        },
    }

    def __init__(self, config, providers: list[FetchProvider] | None = None):
        self.config = config
        self.providers = providers if providers is not None else []

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            url = sanitize_url(params["url"])
        except ValueError as e:
            return ToolResult.text(str(e), is_error=True)
        max_chars = min(params.get("max_chars", DEFAULT_MAX_CHARS), MAX_CHARS_CAP)

        cache_key = f"{url}|{max_chars}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return ToolResult.text(_render(cached), details=cached.__dict__)

        best: FetchResult | None = None
        last_error: str | None = None
        for provider in self.providers:
            if abort.is_set():
                return ToolResult.text("web_fetch aborted.", is_error=True)
            if not provider.available():
                continue
            try:
                result = await provider.fetch(url, max_chars)
            except Exception as e:  # provider failed -> escalate to the next
                last_error = f"{provider.name}: {type(e).__name__}: {e}"
                continue
            if len(result.text.strip()) >= MIN_USEFUL_CHARS:
                _cache_put(cache_key, result)
                return ToolResult.text(_render(result), details=result.__dict__)
            # thin content -> keep the longest so far, try the next provider
            if best is None or len(result.text) > len(best.text):
                best = result

        if best is not None:  # all providers were thin; return the best we got
            _cache_put(cache_key, best)
            return ToolResult.text(_render(best), details=best.__dict__)
        return ToolResult.text(f"web_fetch failed: {last_error or 'no provider available'}", is_error=True)
