"""Relevance-filtered skills (optional, post-parity) — advertise only the skills most semantically
related to the CURRENT message, instead of the whole (budgeted) library.

The skill gate (requires) + budget (cap/compact) already keep a big library from flooding the
prompt; this is the smarter filter ON TOP: embed the user's message and each skill's one-line
"name: description", rank by cosine similarity, and keep the top-K (always-on skills are never
dropped). Pluggable ``embed_fn`` (the model is config-driven) and FAIL-SAFE: any embedding error
falls back to advertising everything, so relevance never costs you a skill.
"""

from __future__ import annotations

import logging
import math

log = logging.getLogger("agentd")


def _cosine(a, b) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0


def rank_skills_by_relevance(skills, query: str, embed_fn, top_k: int):
    """Top-``top_k`` skills most relevant to ``query`` (always-on skills always kept), preserving
    the original order for a stable prompt. ``embed_fn(list[str]) -> list[vector]``. Returns the
    input unchanged when there's nothing to gain (no query/fn, ``top_k<=0``, or already small) or
    on ANY embedding failure (fail-open)."""
    if not skills or not query or embed_fn is None or top_k <= 0 or len(skills) <= top_k:
        return skills
    always = [s for s in skills if getattr(s, "always", False)]
    rankable = [s for s in skills if not getattr(s, "always", False)]
    budget = top_k - len(always)                       # always-on skills consume budget first
    if budget <= 0 or len(rankable) <= budget:
        return skills
    try:
        vecs = embed_fn([query] + [f"{s.name}: {s.description}" for s in rankable])
        qv, svs = vecs[0], vecs[1:]
        ranked = sorted(zip(rankable, svs), key=lambda p: _cosine(qv, p[1]), reverse=True)
    except Exception as e:  # noqa: BLE001 — relevance must never cost a skill
        log.warning("skills: relevance ranking failed, advertising all: %s", e)
        return skills
    keep = {id(s) for s, _ in ranked[:budget]} | {id(s) for s in always}
    chosen = [s for s in skills if id(s) in keep]
    log.info("skills: relevance-filtered %d -> %d (top_k=%d)", len(skills), len(chosen), top_k)
    return chosen


def build_skill_embed_fn(config):
    """A cached embedding fn (litellm) when relevance is enabled AND a model is set; else None.
    Skill texts are stable so they're cached across turns; only the per-message query re-embeds."""
    model = getattr(config, "skills_relevance_model", "") or ""
    if not (getattr(config, "skills_relevance_enabled", False) and model):
        return None
    cache: dict[str, list] = {}

    def embed(texts):
        import litellm
        missing = [t for t in texts if t not in cache]
        if missing:
            resp = litellm.embedding(model=model, input=missing)
            for t, item in zip(missing, resp["data"]):
                cache[t] = item["embedding"]
        return [cache[t] for t in texts]

    return embed
