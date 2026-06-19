"""build_search_providers — assemble the ordered SearchProvider chain from config.

This is the one place that selects + orders search backends. Default order mirrors
OpenClaw's `autoDetectOrder`: gemini (if a Gemini key is present — it reuses the model
key, so it's auto-enabled and primary) -> parallel (keyless hosted Search MCP) ->
duckduckgo (keyless fallback). Brave stays in the registry but is not auto-enabled. An
explicit `config.search_providers` list (env `AGENTD_SEARCH_PROVIDERS`) overrides the
order/selection; unknown names are ignored (forward-compatible with exa/tavily/...).
"""

from __future__ import annotations

import os

from agentd.application.interfaces.search import SearchProvider
from agentd.infrastructure.tools.search.providers import (
    PARALLEL_MCP_SEARCH_URL,
    BraveProvider,
    DuckDuckGoProvider,
    GeminiGroundingProvider,
    ParallelSearchProvider,
)


def _search_model(config) -> str:
    # Grounding runs on a DEDICATED fast model, not the (possibly heavy) main model —
    # a reasoning model makes grounding slow enough to blow the web_search timeout.
    return getattr(config, "search_model", None) or config.model


def _build_parallel(config) -> ParallelSearchProvider:
    # Parallel's keyless hosted Search MCP, reached through the streamable-HTTP MCP
    # session. Connection is lazy (first search) — building the provider is cheap and
    # never touches the network or the `mcp` SDK.
    from types import SimpleNamespace

    from agentd.infrastructure.tools.mcp.session import create_http_session

    url = getattr(config, "parallel_search_url", None) or PARALLEL_MCP_SEARCH_URL
    key = getattr(config, "parallel_api_key", None)
    headers = {"Authorization": f"Bearer {key}"} if key else None
    cfg = SimpleNamespace(name="parallel", transport="http", url=url, headers=headers)

    async def factory():
        return await create_http_session(cfg)

    return ParallelSearchProvider(factory)


def _registry(config) -> dict:
    return {
        "parallel": lambda: _build_parallel(config),
        "gemini": lambda: GeminiGroundingProvider(_search_model(config)),
        "brave": lambda: BraveProvider(config.brave_api_key or ""),
        "duckduckgo": lambda: DuckDuckGoProvider(),
    }


def _default_order(config) -> list[str]:
    # Mirror OpenClaw's autoDetectOrder: gemini (20) -> parallel (76) -> duckduckgo (100).
    # Gemini web search reuses the Gemini model key, so it is auto-enabled and PRIMARY
    # whenever a Gemini key + Gemini search model are present — exactly like OpenClaw.
    # Parallel (keyless) is the primary only when there's no Gemini key.
    order: list[str] = []
    if _search_model(config).startswith("gemini/") and os.environ.get("GEMINI_API_KEY"):
        order.append("gemini")
    if getattr(config, "parallel_search_enabled", True):
        order.append("parallel")
    order.append("duckduckgo")  # keyless last resort, always present
    return order


def build_search_providers(config) -> list[SearchProvider]:
    registry = _registry(config)
    order = getattr(config, "search_providers", None) or _default_order(config)
    return [registry[name]() for name in order if name in registry]
