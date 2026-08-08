"""Per-agent config, layered over the daemon's.

One machine, one `agentd.config.json`, one `.env` — and every agent on it shares them. That is
right for provider keys and wrong for the model: "run my inbox agent on something cheap and my
builder on GPT-5" is an ordinary thing to want, and there was no way to say it.

So the config gains ONE key, shaped exactly like the `plugins` key beside it:

    "agents": {
      "agent-builder": {"override_default": true, "model": "openai/gpt-5"}
    }

Nothing else was needed. `config.get` already returns every exposed key and `config.set`
already merges and hot-applies every writable one, so both directions came free — where a
separate per-agent settings FILE would have meant a second read path and a corrupt-file case
to get wrong.

``override_default`` decides who wins, and it applies KEY BY KEY:

    true (the default)   for each knob: the agent's value if it set one, else the daemon's
    false                the agent's entry is ignored entirely — the daemon's values, whole

Key-by-key matters. All-or-nothing would mean an agent that sets only `model` inherits nothing
else and boots with no reasoning effort, no turn limit, no fallbacks. And it makes the default
safe: an agent with no entry at all — which is every agent today — resolves to exactly the
daemon's values, so turning this on changes nothing until someone sets something.

PROVIDER KEYS ARE ABSENT FROM ``OVERRIDABLE_KEYS`` ON PURPOSE. They live in one shared `.env`;
per-agent copies of the same secret would have no sane precedence, and the rule that strips
`envValues` for an installed agent already assumes a single source.

Pure: a Config in, plain values out. No I/O, so every precedence rule above is a unit test with
no daemon, no filesystem, and no UI.
"""

from __future__ import annotations

# What an agent may decide for itself. Everything else — ports, paths, sub-agent limits,
# provider keys — stays daemon-wide, because it describes the MACHINE rather than this agent's
# behaviour, and an agent offering to change it would be offering to break the install.
OVERRIDABLE_KEYS = frozenset(
    {
        "model",
        "reasoning_effort",
        "max_turns",
        "model_fallbacks",
        "cost_efficiency",
        "verify_tool",
        "memory_enabled",
    }
)

# The flag itself, stored alongside the values in the same per-agent dict.
OVERRIDE_KEY = "override_default"

DAEMON = "daemon"
AGENT = "agent"


def agent_entry(config, agent_id: str) -> dict:
    """This agent's own block from ``config.agents``, or ``{}`` when it has none.

    ``{}`` is not a fallback hiding a failure — it is the normal state of every agent that has
    never had its settings touched, and it resolves to the daemon's values, which is precisely
    how those agents behave today."""
    agents = getattr(config, "agents", None)
    if not isinstance(agents, dict):
        return {}
    entry = agents.get(agent_id)
    return entry if isinstance(entry, dict) else {}


def overrides_daemon(config, agent_id: str) -> bool:
    """Does this agent's own config win? Default TRUE — the flag exists to be turned OFF."""
    return bool(agent_entry(config, agent_id).get(OVERRIDE_KEY, True))


def resolve(config, agent_id: str) -> tuple[dict, dict]:
    """The effective value of every overridable knob for ``agent_id``, and where each came from.

    Returns ``(values, sources)`` where ``sources`` maps each key to ``"agent"`` or
    ``"daemon"``. The second half is not decoration: a settings page that shows a value without
    saying which layer produced it is the same page that showed "GPT-5" while another model
    answered every turn.
    """
    values: dict = {}
    sources: dict = {}
    for key in OVERRIDABLE_KEYS:
        values[key] = getattr(config, key, None)
        sources[key] = DAEMON

    entry = agent_entry(config, agent_id)
    if not entry.get(OVERRIDE_KEY, True):
        return values, sources

    for key, value in entry.items():
        # OVERRIDABLE_KEYS is a whitelist, so `override_default` and anything the user put in
        # this block by hand simply is not a knob — it never reaches the run.
        if key not in OVERRIDABLE_KEYS:
            continue
        values[key] = value
        sources[key] = AGENT
    return values, sources
