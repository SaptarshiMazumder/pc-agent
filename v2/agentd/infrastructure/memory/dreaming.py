"""Dreaming — the periodic memory-consolidation pass (OpenClaw's "deep dreaming", agentd-native).

Run it on a cron/heartbeat (via the ``memory_consolidate`` tool). Three phases, all pure math —
no LLM, no tokens spent:

  1. MERGE near-duplicates — notes whose embeddings are cosine ≥ ``merge_threshold`` collapse to
     the strongest (long-tier first, then newest). Semantic superset of the old exact-dup pass.
  2. PROMOTE short → long — a note the agent keeps recalling, across enough distinct queries and
     with high enough (recency-decayed) score, graduates to durable long-term memory.
  3. FORGET stale shorts — short-tier notes older than ``max_age_days`` that never earned enough
     recalls are dropped, so the store gets *better* over time instead of accreting junk.

Thresholds come from config (``memory_dreaming_*``); ``now`` is injectable for tests.
"""

from __future__ import annotations

import time

from agentd.infrastructure.memory.bank import _cosine

_DAY = 86400.0


def _cfg(config, name, default):
    return getattr(config, name, default)


def dream(bank, agent_id: str, config, now: float | None = None) -> dict:
    """Consolidate one agent's memory. Returns counts: {merged, promoted, forgotten}."""
    now = time.time() if now is None else now
    merged = _merge_near_dupes(bank, agent_id,
                               _cfg(config, "memory_dreaming_merge_threshold", 0.92))

    min_score = _cfg(config, "memory_dreaming_min_score", 0.8)
    min_recall = _cfg(config, "memory_dreaming_min_recall_count", 3)
    min_uniq = _cfg(config, "memory_dreaming_min_unique_queries", 3)
    half_life = max(1e-9, _cfg(config, "memory_dreaming_recency_half_life_days", 14.0)) * _DAY
    max_age = _cfg(config, "memory_dreaming_max_age_days", 30) * _DAY

    aggs = bank.recall_aggregates(agent_id)
    promoted = forgotten = 0
    for row in bank.rows_for(agent_id):
        if row.tier == "long":
            continue
        agg = aggs.get(row.id)
        if agg and agg.count >= min_recall and agg.unique_queries >= min_uniq:
            # recency-decayed confidence: a note recalled long ago counts for less
            anchor = row.last_recalled or row.created_at
            recency = 0.5 ** ((now - anchor) / half_life)
            if agg.max_score * recency >= min_score:
                bank.set_tier(row.id, "long")
                promoted += 1
                continue
        # never stuck and past its shelf life -> forget
        if (now - row.created_at) > max_age and row.recall_count < min_recall:
            bank.delete(row.id)
            forgotten += 1
    return {"merged": merged, "promoted": promoted, "forgotten": forgotten}


def _merge_near_dupes(bank, agent_id: str, threshold: float) -> int:
    """Collapse embedding-near-duplicate notes to the strongest survivor. O(n²) over the agent's
    rows — fine at memory scale; the winner is long-tier-first then newest."""
    rows = [r for r in bank.rows_for(agent_id) if r.embedding is not None]
    removed = 0
    dropped: set[str] = set()
    for i, a in enumerate(rows):
        if a.id in dropped:
            continue
        for b in rows[i + 1:]:
            if b.id in dropped:
                continue
            if _cosine(a.embedding, b.embedding) >= threshold:
                keep, drop = _stronger(a, b)
                if bank.delete(drop.id):
                    removed += 1
                    dropped.add(drop.id)
                    if drop.id == a.id:      # a itself was dropped; stop pairing it
                        break
    return removed


def _stronger(a, b):
    """The keeper of a near-dup pair: long-tier wins; then more recalls; then newer."""
    a_key = (a.tier == "long", a.recall_count, a.created_at)
    b_key = (b.tier == "long", b.recall_count, b.created_at)
    return (a, b) if a_key >= b_key else (b, a)
