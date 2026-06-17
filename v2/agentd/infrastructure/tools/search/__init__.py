"""web_search adapters: provider chain, cache, formatting, factory."""

from agentd.infrastructure.tools.search.cache import cache_get, cache_put
from agentd.infrastructure.tools.search.factory import build_search_providers
from agentd.infrastructure.tools.search.format import format_results
from agentd.infrastructure.tools.search.providers import (
    BraveProvider,
    DuckDuckGoProvider,
    GeminiGroundingProvider,
)

__all__ = [
    "build_search_providers",
    "format_results",
    "cache_get",
    "cache_put",
    "BraveProvider",
    "DuckDuckGoProvider",
    "GeminiGroundingProvider",
]
