"""Shared embedding helpers (litellm-backed) — used by BOTH skills relevance and long-term
memory. Provider-neutral: point the model at anything litellm supports —
``gemini/text-embedding-004`` (default for memory), ``text-embedding-3-small`` (OpenAI),
or a local ``ollama/nomic-embed-text`` (no API key). One seam, swap the model in config.

FAIL-OPEN by contract: callers treat a ``None`` embed_fn or any embedding error as "no
vectors" and fall back to keyword search — semantic recall is an enhancement, never a hard
dependency, so a missing key or an offline embedder never breaks a turn.
"""

from __future__ import annotations

import logging
import math
import threading

log = logging.getLogger("agentd")


def cosine(a, b) -> float:
    """Cosine similarity of two equal-length vectors (0.0 when either is degenerate)."""
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0


def build_embed_fn(model: str, cache: dict | None = None):
    """An ``embed_fn(list[str]) -> list[vector]`` backed by litellm, or ``None`` when ``model``
    is empty. Optional ``cache`` memoizes stable texts (skill descriptions, memory notes) so
    only novel text hits the network; the per-message query is never cached and re-embeds each
    call. Raises on a genuine embedding failure — callers wrap in try/except and fall open."""
    model = (model or "").strip()
    if not model:
        return None
    store: dict = cache if cache is not None else {}
    lock = threading.Lock()  # the cache is shared across the loop thread (recall) AND worker
    # threads (background writes) — guard dict access without holding
    # the lock across the network call

    def embed(texts):
        import litellm

        with lock:
            missing = [t for t in texts if t not in store]
        if missing:
            resp = litellm.embedding(model=model, input=missing)  # network — no lock held
            with lock:
                for t, item in zip(missing, resp["data"]):
                    store[t] = item["embedding"]
        with lock:
            return [store[t] for t in texts]

    return embed
