"""How much context a model will accept, so a client can show how much is left.

WHY THIS IS WORTH A MODULE. A conversation that outgrows its model does not fail loudly: the
provider returns an empty response, the runtime's incomplete-turn retry appends ANOTHER message
and re-sends, and the user watches the same "couldn't generate a response" twice with nothing on
screen to explain it. The number that would have explained it is knowable the whole time.

THE USED HALF IS ALREADY EXACT, and is not computed here: every assistant message carries
``usage["input"]`` — the tokens the provider actually billed for the request that produced it. No
estimator can beat that, so nothing here tries. This module supplies only the DENOMINATOR.

UNKNOWN IS A REAL ANSWER. LiteLLM's table does not know every model — a proxy alias, a fine-tune,
something released last week. Returning 0 for those would be read as "no room at all" and would
show a full meter on the first turn of an empty chat; guessing a number would be worse, because it
would be wrong quietly. So an unknown model yields ``None`` and every caller is written to render
nothing rather than a lie.
"""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")

#: Answers we have already worked out this process. The lookup walks LiteLLM's model map and is
#: called once per assistant message, on a hot path — and the answer for a given model cannot
#: change while the process lives.
_cache: dict[str, int | None] = {}

#: Stripped before asking LiteLLM. Routing prefixes name WHERE a request goes, never WHAT model
#: answers it, and the table is keyed by the latter — so `litellm_proxy/claude-sonnet-4` is
#: unknown while `claude-sonnet-4` is not. Without this every proxied deployment (which is every
#: hosted one) would report an unknown limit and show no meter at all.
_ROUTING_PREFIXES = ("litellm_proxy/", "openrouter/", "azure/", "vertex_ai/", "bedrock/")


def max_input_tokens(model: str) -> int | None:
    """The model's input budget, or None when it cannot be established.

    :param model: the model id as the runtime names it, routing prefix and all.
    """
    model = (model or "").strip()
    if not model:
        return None
    if model in _cache:
        return _cache[model]
    _cache[model] = _lookup(model)
    return _cache[model]


def _lookup(model: str) -> int | None:
    try:
        import litellm
    except Exception:  # pragma: no cover - litellm is a hard dependency of the LLM path
        return None

    # `model_cost`, not `get_model_info`. The function RAISES on an unknown model and prints a
    # provider list to stderr on the way out — and an unknown model is the ordinary case here, not
    # an error. Asked once per new model that is not in the table, that noise would appear in the
    # daemon's log looking like a fault. The dict is the same data, looked up silently.
    table = getattr(litellm, "model_cost", None) or {}
    for candidate in _candidates(model):
        row = table.get(candidate)
        if not isinstance(row, dict):
            continue
        # `max_input_tokens` is the one we want. `max_tokens` on many rows means max OUTPUT, so
        # reading it as the context window understates a 200k model as an 8k one.
        limit = row.get("max_input_tokens") or 0
        if limit > 0:
            return int(limit)
    log.debug("context: no known input limit for model %r", model)
    return None


def _candidates(model: str) -> list[str]:
    """The names to try, most specific first: the model as given, then with any routing prefix
    removed, then the bare name after the last slash."""
    out = [model]
    for prefix in _ROUTING_PREFIXES:
        if model.startswith(prefix):
            out.append(model[len(prefix) :])
            break
    if "/" in model:
        tail = model.rsplit("/", 1)[1]
        if tail and tail not in out:
            out.append(tail)
    return out
