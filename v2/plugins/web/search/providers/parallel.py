"""ParallelSearchProvider — web_search backed by Parallel's keyless hosted Search MCP.

This is the search backend OpenClaw uses as its zero-config default
(https://search.parallel.ai/mcp — free tier needs NO API key, streamable-HTTP). We
reach it through the same MCP session machinery as any hosted MCP server, then
adapt its `web_search` tool to the `SearchProvider` port so our `web_search` tool
stays a single-`query` interface (the agent never sees Parallel's two-field schema).

Parallel's `web_search` contract (discovered live):
  in : {objective: str, search_queries: [str], session_id: str(reused uuid)}
  out: one text block of JSON -> {"results": [{url, title, publish_date, excerpts:[...]}]}
`search_queries` may include search operators (e.g. `site:`), so Parallel handles
people/site queries natively — the reason it works where Gemini grounding doesn't.
"""

from __future__ import annotations

import json
import logging
import uuid

from agent_runtime.application.interfaces.search import SearchResult

log = logging.getLogger("agentd")

PARALLEL_MCP_SEARCH_URL = "https://search.parallel.ai/mcp"


class ParallelSearchProvider:
    name = "parallel"

    def __init__(self, session_factory, *, tool_name: str = "web_search", max_snippet: int = 700):
        # session_factory: async () -> McpSession (already started + initialized).
        self._session_factory = session_factory
        self._tool_name = tool_name
        self._max_snippet = max_snippet
        self._session = None
        # One stable id per provider instance, reused on every call so Parallel's
        # free-tier rate-limiting/log-correlation sees a single conversation.
        self._session_id = uuid.uuid4().hex

    def available(self) -> bool:
        # Keyless + connect-on-demand. Only gated on the optional `mcp` SDK; if a
        # search later raises (offline / rate-limited), the chain falls through to
        # the next provider (DuckDuckGo).
        try:
            import mcp  # noqa: F401
        except ImportError:
            return False
        return True

    async def _ensure_session(self):
        if self._session is None:
            self._session = await self._session_factory()
        return self._session

    async def search(self, query: str, count: int, freshness: str | None) -> list[SearchResult]:
        session = await self._ensure_session()
        args = {
            "objective": query,
            "search_queries": [query],
            "session_id": self._session_id,
        }
        result = await session.call_tool(self._tool_name, args)
        if result.is_error:
            detail = result.content[0].text if result.content else ""
            raise RuntimeError(f"parallel web_search error: {detail[:200]}")
        return self._parse(result, count)

    def _parse(self, result, count: int) -> list[SearchResult]:
        text = "".join(b.text for b in (result.content or []) if getattr(b, "text", None))
        if not text.strip():
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("parallel: web_search returned non-JSON content; skipping")
            return []
        out: list[SearchResult] = []
        for r in (data.get("results") or [])[:count]:
            excerpts = [e.strip() for e in (r.get("excerpts") or []) if e and e.strip()]
            snippet = " ".join(excerpts)
            if len(snippet) > self._max_snippet:
                snippet = snippet[: self._max_snippet].rstrip() + "…"
            out.append(
                SearchResult(
                    title=r.get("title") or "",
                    url=r.get("url") or "",
                    snippet=snippet,
                    age=r.get("publish_date") or "",
                )
            )
        return out

    async def aclose(self) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass
            self._session = None
