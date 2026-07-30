"""GeminiGroundingProvider — web search via Gemini's native Google-Search grounding.

Faithful port of OpenClaw's `gemini-web-search-provider.runtime.ts`: a direct
`generateContent` POST with `tools:[{google_search:{}}]`, billed to the Gemini key.
It reuses the *model* key (no separate search key), which is why OpenClaw auto-selects
gemini as the default web search whenever a Gemini key is present (autoDetectOrder 20,
ahead of Parallel 76 / DuckDuckGo 100).

Two things we now match from OpenClaw that the earlier version got wrong (and which
made `site:`-style queries fall through to DuckDuckGo with no usable URLs):

1. **Return the synthesized ANSWER whenever it's non-empty — even with zero grounding
   chunks.** OpenClaw keys success on `content`, not on the presence of chunks. The old
   code did `if not chunks: return []`, discarding Gemini's answer and falling through.
2. **Resolve each grounding-chunk redirect URL** (`vertexaisearch.../grounding-api-
   redirect/…`) to its real source URL via a HEAD request (OpenClaw's
   `resolveCitationRedirectUrl`), so citations are real `linkedin.com/…` links — not
   opaque Google redirects.
"""

from __future__ import annotations

import asyncio
import logging
import os

from agent_runtime.application.interfaces.search import SearchResult

log = logging.getLogger("agentd")

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiGroundingProvider:
    name = "gemini"

    def __init__(self, model: str, api_key: str | None = None):
        self._model = model  # e.g. "gemini/gemini-2.5-flash"
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")

    def available(self) -> bool:
        return self._model.startswith("gemini/") and bool(self._api_key)

    async def search(self, query: str, count: int, freshness: str | None) -> list[SearchResult]:
        import httpx

        model = self._model.split("/", 1)[1] if "/" in self._model else self._model
        url = f"{_GEMINI_BASE}/models/{model}:generateContent"
        body = {
            "contents": [{"parts": [{"text": self._augment(query, freshness)}]}],
            "tools": [{"google_search": {}}],
        }
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                url,
                headers={"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
                json=body,
            )
            resp.raise_for_status()
            return await self._parse(client, resp.json(), count)

    @staticmethod
    def _augment(query: str, freshness: str | None) -> str:
        # Gemini grounding has no freshness param; fold it into the query text.
        if freshness:
            return f"{query} (focus on results from the last {freshness})"
        return query

    async def _parse(self, client, data, count: int) -> list[SearchResult]:
        candidates = data.get("candidates") or []
        if not candidates or not isinstance(candidates[0], dict):
            return []
        cand = candidates[0]
        parts = ((cand.get("content") or {}).get("parts")) or []
        content = "\n".join(
            p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)
        ).strip()

        gm = cand.get("groundingMetadata") or {}
        raw: list[tuple[str, str | None]] = []
        for c in gm.get("groundingChunks") or []:
            web = c.get("web") if isinstance(c, dict) else None
            if isinstance(web, dict) and isinstance(web.get("uri"), str):
                raw.append((web["uri"], web.get("title")))
        resolved = await self._resolve_redirects(client, [u for u, _ in raw])

        results: list[SearchResult] = []
        if content:
            # The synthesized answer (with inline web data) — OpenClaw returns this as
            # the primary `content`, even when there are no citations.
            results.append(SearchResult(title="Gemini grounded answer", url="", snippet=content))
        for (_uri, title), real in list(zip(raw, resolved))[:count]:
            results.append(SearchResult(title=title or real, url=real))
        return results

    @staticmethod
    async def _resolve_redirects(client, urls: list[str]) -> list[str]:
        # Follow each grounding redirect to its real source URL (OpenClaw uses HEAD;
        # on any failure keep the original URL so a flaky redirect never drops a hit).
        async def one(u: str) -> str:
            try:
                r = await client.head(u, follow_redirects=True, timeout=8.0)
                return str(r.url) or u
            except Exception:  # noqa: BLE001
                return u

        if not urls:
            return []
        return await asyncio.gather(*[one(u) for u in urls])
