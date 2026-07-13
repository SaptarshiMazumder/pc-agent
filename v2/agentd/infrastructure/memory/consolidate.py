"""Memory consolidation — the lite "Dreaming" deep-phase (Phase 3 / S6).

Collapses exact-duplicate notes for an agent (keeps the newest of each), so a fact the
agent remembers repeatedly doesn't bloat the bank. A cron/heartbeat job can run it
periodically. (LLM-driven summarization + promotion into MEMORY.md is a later refinement.)
"""

from __future__ import annotations


def consolidate(bank, agent_id: str, limit: int = 500) -> int:
    """Remove exact-duplicate notes for ``agent_id`` (case/space-insensitive), keeping the
    most recent occurrence. Returns how many were removed."""
    seen: set[str] = set()
    removed = 0
    for item in bank.recent(agent_id, limit=limit):  # newest-first -> first seen is the keeper
        key = " ".join(item.text.split()).lower()
        if key in seen:
            if bank.delete(item.id):
                removed += 1
        else:
            seen.add(key)
    return removed
