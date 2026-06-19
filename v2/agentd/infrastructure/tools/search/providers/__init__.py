"""SearchProvider adapters — the only place a search backend is known."""

from agentd.infrastructure.tools.search.providers.brave import BraveProvider
from agentd.infrastructure.tools.search.providers.duckduckgo import DuckDuckGoProvider
from agentd.infrastructure.tools.search.providers.gemini import GeminiGroundingProvider
from agentd.infrastructure.tools.search.providers.parallel import (
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
