"""Render normalized results into the model-facing text (same strings as before,
now reading SearchResult attributes instead of dict keys)."""

from __future__ import annotations

from agent_runtime.application.interfaces.search import SearchResult


def format_results(results: list[SearchResult], provider: str) -> str:
    if not results:
        return f"No results (provider: {provider})."
    lines = []
    for i, r in enumerate(results, 1):
        age = f"  ({r.age})" if r.age else ""
        url = f"\n   {r.url}" if r.url else ""
        snippet = f"\n   {r.snippet}" if r.snippet else ""
        lines.append(f"{i}. {r.title}{age}{url}{snippet}")
    return f"Search results (provider: {provider}):\n\n" + "\n\n".join(lines)
