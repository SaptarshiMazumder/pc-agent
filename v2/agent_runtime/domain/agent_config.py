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

THE AGENT'S OWN SETTINGS ALWAYS WIN, and they apply KEY BY KEY: for each knob, the agent's value
if it set one, else the daemon's.

Key-by-key matters. All-or-nothing would mean an agent that sets only `model` inherits nothing
else and boots with no reasoning effort, no turn limit, no fallbacks. And it makes the default
safe: an agent with no entry at all resolves to exactly the daemon's values, so this changes
nothing until someone sets something.

THERE IS NO OVERRIDE SWITCH. There was one, and it was a trap: "off" meant "use the daemon's
values", which combined with cost-efficiency — a knob that OVERWRITES the model every turn —
produced an agent that named its model, watched the daemon's cheap one answer anyway, and had
nothing on screen to explain it. One layer decides, so there is nothing to arbitrate.

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
    """Does this agent's own config win? ALWAYS. Kept as a function because callers ask, and
    because answering here is cheaper than deleting the question from four places.

    IT USED TO BE A SETTING, and the setting was a trap. "Override JARVIS settings: off" read as
    "use the daemon's values", which is what it did — but combined with a knob that OVERWRITES the
    model every turn (cost efficiency), an agent could set its model, watch the daemon's cheap one
    answer anyway, and have no way to see why. An agent's own settings decide how that agent runs;
    there is no second answer to arbitrate, so there is no switch.
    """
    return True


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

    # NO GATE. An agent's own settings decide how that agent runs — see `overrides_daemon` for
    # why the switch that used to sit here is gone. A stored `override_default` from before is
    # simply not a knob (the whitelist below drops it), so an old config keeps working.
    entry = agent_entry(config, agent_id)
    for key, value in entry.items():
        # OVERRIDABLE_KEYS is a whitelist, so `override_default` and anything the user put in
        # this block by hand simply is not a knob — it never reaches the run.
        if key not in OVERRIDABLE_KEYS:
            continue
        values[key] = value
        sources[key] = AGENT
    return values, sources
