"""build_search_providers — assemble the ordered SearchProvider chain from config.

This is the one place that selects + orders search backends. Default order
(Gemini first): gemini (if on a Gemini model with a key) -> brave (if key) ->
duckduckgo (always). An explicit `config.search_providers` list (env
`AGENTD_SEARCH_PROVIDERS`) overrides the order/selection; unknown names are
ignored (forward-compatible with tavily/exa/... later).
"""

from __future__ import annotations

import os

from agentd.application.interfaces.search import SearchProvider
from agentd.infrastructure.tools.search.providers import (
    BraveProvider,
    DuckDuckGoProvider,
    GeminiGroundingProvider,
)


def _registry(config) -> dict:
    return {
        "gemini": lambda: GeminiGroundingProvider(config.model),
        "brave": lambda: BraveProvider(config.brave_api_key or ""),
        "duckduckgo": lambda: DuckDuckGoProvider(),
    }


def _default_order(config) -> list[str]:
    order: list[str] = []
    if config.model.startswith("gemini/") and os.environ.get("GEMINI_API_KEY"):
        order.append("gemini")
    if config.brave_api_key:
        order.append("brave")
    order.append("duckduckgo")  # keyless last resort, always present
    return order


def build_search_providers(config) -> list[SearchProvider]:
    registry = _registry(config)
    order = getattr(config, "search_providers", None) or _default_order(config)
    return [registry[name]() for name in order if name in registry]
