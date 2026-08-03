"""Metering: turn a provider cost into credits, enforce the cap, and never lose a billing row.

Lives next to custom_auth.py (litellm resolves `custom_auth.<attr>` relative to the config file,
and this is imported from there). Split out because auth answers "who are you?" while this
answers "can you afford it, and what did it cost?" — different questions, different failure
modes, different tests.

THE CREDIT MODEL
    credits = ceil(provider_cost_usd * CREDITS_PER_USD)

One config number, and cost comes from litellm's own per-model pricing. That is deliberately
NOT a hand-maintained "opus = 60x deepseek" multiplier table, because such a table:
  * goes stale the moment a provider changes prices,
  * has no entry for a model added tomorrow, and
  * cannot express that a model's OUTPUT tokens cost 5x its input, or that a cache read is 10%
    of a normal read — both of which change the true multiplier per call.
Deriving from cost handles all of that for free, including models that do not exist yet.

Sizing the dial: if $20 should buy 1,000,000 credits at a 30% cost ratio, then those credits
must map to $6 of provider spend, so CREDITS_PER_USD = 1_000_000 / 6 ~= 166_667.

MODEL TIERS are data too (AGENTD_MODEL_TIERS), because a free agent's creator has no cost
incentive at all — they gain nothing by being efficient and lose nothing by choosing the most
expensive model available. Restraint has to be structural, not requested.
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import deque

import httpx

log = logging.getLogger("model_proxy.metering")

# --- credits ----------------------------------------------------------------

_FALLBACK_CREDITS_PER_USD = 166_667.0


def credits_per_usd() -> float:
    try:
        value = float(os.environ.get("AGENTD_CREDITS_PER_USD", "") or _FALLBACK_CREDITS_PER_USD)
    except ValueError:
        return _FALLBACK_CREDITS_PER_USD
    return value if value > 0 else _FALLBACK_CREDITS_PER_USD


def credits_for(cost_usd: float) -> int:
    """Whole credits for one call. Rounds UP with a floor of 1, so a real call is never free.

    Per-call rounding (rather than accumulating fractions across a run) over-charges by well
    under a cent per run at any sane dial setting, and it needs no per-run state in the proxy —
    which would otherwise be an unbounded dict keyed by run_id with no reliable end-of-run
    signal to clean it up. Revisit only if the share of sub-credit calls grows a lot.
    """
    if cost_usd <= 0:
        return 0
    return max(1, math.ceil(cost_usd * credits_per_usd()))


# --- model tiers -------------------------------------------------------------
#
# AGENTD_MODEL_TIERS: {"substring": "tier"} matched against the model name, first hit wins.
# AGENTD_MODEL_TIER_ORDER: cheapest -> most expensive. A grant's model_tier_max names the most
# expensive tier it may spend on; anything beyond that is refused BEFORE the provider is called.

_DEFAULT_ORDER = ("cheap", "standard", "premium")


def tier_order() -> tuple[str, ...]:
    raw = os.environ.get("AGENTD_MODEL_TIER_ORDER", "").strip()
    if not raw:
        return _DEFAULT_ORDER
    parts = tuple(p.strip() for p in raw.split(",") if p.strip())
    return parts or _DEFAULT_ORDER


def _tier_map() -> dict:
    raw = os.environ.get("AGENTD_MODEL_TIERS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        log.warning("AGENTD_MODEL_TIERS ignored: invalid JSON")
        return {}


def tier_for(model: str) -> str:
    """The tier of a model name. Unknown models get the MIDDLE tier, not the cheapest.

    Defaulting unknown-to-cheapest would mean any newly added model silently becomes spendable
    on a cheap-only grant; defaulting to the most expensive would block ordinary use until
    someone edits config. The middle is the least surprising failure in both directions.
    """
    name = (model or "").lower()
    for needle, tier in _tier_map().items():
        if str(needle).lower() in name:
            return str(tier)
    order = tier_order()
    return order[len(order) // 2] if order else ""


def tier_allowed(model: str, tier_max: str) -> bool:
    """Is `model` within a grant's ceiling? An empty ceiling means no restriction."""
    if not tier_max:
        return True
    order = tier_order()
    try:
        return order.index(tier_for(model)) <= order.index(tier_max)
    except ValueError:  # an unknown tier name in config — fail OPEN, never block a paying user
        return True


# --- the ledger retry buffer (plan 1.6) --------------------------------------
#
# Swallowing a failed usage post is correct: a billing outage must never break a chat. What was
# missing is that nobody replayed it, so an Accounts blip meant permanently unrecorded spend.
# This holds failures and re-posts them on the next successful call.
#
# LIMIT, stated plainly: in-memory, so a task replacement loses whatever is buffered. Durable
# replay needs SQS or a disk-backed queue (Phase 3). The bounded depth is deliberate — an
# unbounded retry queue turns a downstream outage into an out-of-memory kill.

_MAX_BUFFER = int(os.environ.get("AGENTD_LEDGER_BUFFER_MAX", "500") or 500)
_buffer: deque = deque(maxlen=_MAX_BUFFER)


def buffer_row(row: dict) -> int:
    """Park a failed ledger write. Returns the new depth (a metric worth alarming on)."""
    _buffer.append(row)
    return len(_buffer)


def buffer_depth() -> int:
    return len(_buffer)


async def drain_buffer(client: httpx.AsyncClient, internal_key: str, limit: int = 25) -> tuple[int, int]:
    """Re-post buffered rows. Returns (replayed, still_buffered).

    Stops at the FIRST failure and puts that row back at the front: if Accounts is still down,
    hammering it with the whole backlog helps nobody, and preserving order keeps the ledger
    readable. Called opportunistically after a successful post, so recovery needs no timer.
    """
    replayed = 0
    for _ in range(min(limit, len(_buffer))):
        row = _buffer.popleft()
        try:
            r = await client.post("/usage", headers={"X-Internal-Key": internal_key}, json=row)
            if r.status_code != 200:
                _buffer.appendleft(row)
                break
            replayed += 1
        except httpx.HTTPError:
            _buffer.appendleft(row)
            break
    if replayed:
        log.info("ledger replay: %d row(s) recovered, %d still buffered", replayed, len(_buffer))
    return replayed, len(_buffer)
