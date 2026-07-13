"""SearchProvider adapters — the only place a search backend is known."""

from search.providers.brave import BraveProvider
from search.providers.duckduckgo import DuckDuckGoProvider
from search.providers.gemini import GeminiGroundingProvider
from search.providers.parallel import (
    PARALLEL_MCP_SEARCH_URL,
    ParallelSearchProvider,
)

__all__ = [
    "BraveProvider",
    "DuckDuckGoProvider",
    "GeminiGroundingProvider",
    "ParallelSearchProvider",
    "PARALLEL_MCP_SEARCH_URL",
]
