"""SearchProvider + SearchResult — the contract for a web-search backend.

The `web_search` tool is a thin dispatcher: it runs an ordered CHAIN of providers
(first non-empty wins) and never knows which backend produced the results. Each
backend — Gemini Google-Search grounding, Brave, DuckDuckGo, ... — is an adapter
implementing THIS port. Which providers, and in what order, is decided by config
in the composition/factory layer, not here and not in the tool.

This is the same ports-and-adapters shape as `skills.py` / `llm.py`: the contract
lives in the application layer (no IO, no backend imports); the infrastructure
layer provides the implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SearchResult:
    """One normalized search hit. `snippet` may be empty (e.g. a citation with no
    description); `age` is "" when the provider gives no recency info."""

    title: str
    url: str
    snippet: str = ""
    age: str = ""


@runtime_checkable
class SearchProvider(Protocol):
    """A web-search backend.

    `name` is the short id shown as `provider: <name>` and used in the
    `AGENTD_SEARCH_PROVIDERS` config. `available()` is a cheap runtime guard (key
    present, optional dep importable) so the chain can skip a provider without
    rebuilding it. `search` returns normalized results; an empty list means "no
    results" (the chain falls through to the next provider), while raising signals
    an error (the dispatcher records it and tries the next provider).
    """

    name: str

    def available(self) -> bool: ...

    async def search(
        self, query: str, count: int, freshness: str | None
    ) -> list[SearchResult]: ...
