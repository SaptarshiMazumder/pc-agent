"""web_search adapters: provider chain, cache, formatting, factory."""

from search.cache import cache_get, cache_put
from search.factory import build_search_providers
from search.format import format_results
from search.providers import (
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
