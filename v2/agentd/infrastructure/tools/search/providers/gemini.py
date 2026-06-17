"""GeminiGroundingProvider — web search via Gemini's native Google-Search
grounding (the OpenClaw `gemini-web-search-provider` equivalent).

The search runs INSIDE a Gemini API call (billed to the Gemini key, generous free
allowance), so no separate search key is needed. We use LiteLLM (Option A): pass
`web_search_options={}`, which LiteLLM maps to Gemini's `tools=[{googleSearch:{}}]`.

Response shape (verified via spike, LiteLLM >= 1.60):
- synthesized answer: ``resp.choices[0].message.content``
- citations: ``resp.vertex_ai_grounding_metadata[0]["groundingChunks"][i]["web"]``
  with ``{"uri": ..., "title": ...}``

We return the synthesized **answer** (as a pseudo-result) plus one **citation** per
grounding chunk. If the model answered WITHOUT grounding (no chunks), we return []
so the dispatcher falls through to a real search provider — i.e. this provider only
"wins" when it actually searched.

Option B (fallback, not needed here): a direct httpx POST to
`{base}/models/{model}:generateContent` with `tools:[{google_search:{}}]`, parsing
`candidates[0].groundingMetadata.groundingChunks[].web` — mirrors
reference/openclaw-main/extensions/google/src/gemini-web-search-provider.runtime.ts.
"""

from __future__ import annotations

import os

from agentd.application.interfaces.search import SearchResult


class GeminiGroundingProvider:
    name = "gemini"

    def __init__(self, model: str, api_key: str | None = None):
        self._model = model  # e.g. "gemini/gemini-2.5-pro"
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")

    def available(self) -> bool:
        return self._model.startswith("gemini/") and bool(self._api_key)

    async def search(self, query: str, count: int, freshness: str | None) -> list[SearchResult]:
        import litellm

        resp = await litellm.acompletion(
            model=self._model,
            messages=[{"role": "user", "content": self._augment(query, freshness)}],
            web_search_options={},
            allowed_openai_params=["web_search_options"],
            stream=False,
        )
        return self._parse(resp, count)

    @staticmethod
    def _augment(query: str, freshness: str | None) -> str:
        # Gemini grounding has no freshness param; fold it into the query text.
        if freshness:
            return f"{query} (focus on results from the last {freshness})"
        return query

    @staticmethod
    def _parse(resp, count: int) -> list[SearchResult]:
        chunks = GeminiGroundingProvider._grounding_chunks(resp)
        if not chunks:
            return []  # not grounded -> let the chain fall through to a real search
        citations: list[SearchResult] = []
        for c in chunks:
            web = (c or {}).get("web") or {}
            uri = web.get("uri")
            if not uri:
                continue
            citations.append(SearchResult(title=web.get("title") or uri, url=uri))
        results: list[SearchResult] = []
        try:
            answer = (resp.choices[0].message.content or "").strip()
        except (AttributeError, IndexError):
            answer = ""
        if answer:
            results.append(SearchResult(title="Gemini grounded answer", url="", snippet=answer))
        results.extend(citations[:count])
        return results

    @staticmethod
    def _grounding_chunks(resp) -> list:
        gm = getattr(resp, "vertex_ai_grounding_metadata", None)
        if not gm:
            return []
        md = gm[0] if isinstance(gm, list) and gm else gm
        if not isinstance(md, dict):
            return []
        return md.get("groundingChunks") or md.get("grounding_chunks") or []
