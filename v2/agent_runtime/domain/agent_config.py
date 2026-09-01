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

THREE LAYERS, KEY BY KEY. For each knob: the INSTALLER's value if they set one, else the
AUTHOR's if the agent shipped one, else the daemon's.

    agents.<id>.<key>   the INSTALLER's, in their own agentd.config.json. Never travels, and
                        survives an agent being updated.
    agent.config.json   the AUTHOR's, shipped INSIDE the agent package. An author who needs a
                        vision model for their agent to work at all can say so once, and every
                        person who installs it gets that rather than whatever their daemon
                        happened to be set to. Replaced wholesale when the agent is upgraded.
    the daemon          everything neither of them decided.

Two layers, and the author's was the missing one: before it, an agent could describe what it was
but not how it had to run, so it arrived on a stranger's machine configured by a stranger.

`user_editable` DECIDES WHETHER LAYER 1 MAY BEAT LAYER 2, and it defaults to false — the author's
choices are the author's. It applies only to keys the author actually SET: a knob they never
touched is not locked, because there is nothing there to protect. That distinction is what stops
"my agent needs Gemini for vision" from also meaning "and you may not change the turn limit".

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

#: Where an agent's declared `[[settings]]` VALUES sit inside `agent.config.json`.
SETTINGS_KEY = "settings"

DAEMON = "daemon"
#: The AUTHOR's layer — `agent.config.json`, shipped inside the package.
AUTHOR = "author"
#: The INSTALLER's layer. Named `agent` for the settings page that already reads this word, and
#: because from the daemon's side it IS "this agent's block in my config".
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


def authored_values(authored: dict | None) -> dict:
    """The knobs an agent's own `agent.config.json` decided.

    A WHITELIST, exactly like the installer's block below. The file also carries `user_editable`
    and a `settings` table of the agent's declared values, neither of which is a run knob — and an
    author who writes `port` into it must not be able to move the daemon's port on somebody else's
    machine by shipping an agent.
    """
    if not isinstance(authored, dict):
        return {}
    return {k: v for k, v in authored.items() if k in OVERRIDABLE_KEYS}


def user_may_edit(authored: dict | None) -> bool:
    """May the INSTALLER change what the author decided?

    DEFAULTS TO FALSE, which is the answer that makes an agent behave the same everywhere. An
    author who wants their settings to be a starting point rather than a rule says so once.

    Only what the author SET is locked — see `resolve`.
    """
    return bool(isinstance(authored, dict) and authored.get("user_editable"))


def strip_secret_settings(authored: dict | None, secret_keys) -> tuple[dict, list[str]]:
    """The authored config with every SECRET setting value removed, and the names removed.

    WHY THIS EXISTS. The author's config ships inside the package, and the same file holds the
    values whoever runs the agent typed into its settings page. One of those is meant to travel
    and the other is a credential. Publishing an agent must not upload the author's API key
    because they happened to fill in their own settings while building it.

    The DECLARATION decides what is a secret — `[[settings]] kind = "secret"` in agent.toml — so
    the two facts stay in one place. A key the agent never declared is not stripped, because
    nothing said it was a credential and silently deleting an author's value would be worse than
    the thing this prevents.

    Pure, and returns what it removed: the packer verifies its own output afterwards, and a
    verification that cannot say what should have gone is a verification that cannot fail usefully.
    """
    if not isinstance(authored, dict):
        return {}, []
    table = authored.get(SETTINGS_KEY)
    if not isinstance(table, dict):
        return dict(authored), []
    secrets = {str(k) for k in (secret_keys or ())}
    removed = sorted(k for k in table if str(k) in secrets)
    if not removed:
        return dict(authored), []
    out = dict(authored)
    out[SETTINGS_KEY] = {k: v for k, v in table.items() if str(k) not in secrets}
    return out, removed


def secret_values_present(authored: dict | None, secret_keys) -> list[str]:
    """Which secret settings still carry a value. The packer's own check on what it just wrote.

    A FILTER YOU HAVE TO TRUST IS WHAT MAKES ONE FILE RISKY. Verifying the artifact afterwards
    turns "the stripper is correct" into "the package is clean", which is a different and much
    smaller thing to be sure of.
    """
    _, removed = strip_secret_settings(authored, secret_keys)
    return removed


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


def resolve(config, agent_id: str, authored: dict | None = None) -> tuple[dict, dict]:
    """The effective value of every overridable knob for ``agent_id``, and where each came from.

    Returns ``(values, sources)`` where ``sources`` maps each key to ``"agent"``, ``"author"`` or
    ``"daemon"``. The second half is not decoration: a settings page that shows a value without
    saying which layer produced it is the same page that showed "GPT-5" while another model
    answered every turn — and with three layers there are now two ways to be surprised by a value
    you did not set.

    ``authored`` is the parsed ``agent.config.json``, or None for an agent that ships none (which
    is every agent built before this existed, and every agent whose author had no opinion). Passed
    IN rather than read here, because this module is pure and a file read is not.
    """
    values: dict = {}
    sources: dict = {}
    for key in OVERRIDABLE_KEYS:
        values[key] = getattr(config, key, None)
        sources[key] = DAEMON

    # LAYER 2: what the agent's author shipped. Applied before the installer's so that the
    # installer's can beat it — when they are allowed to.
    for key, value in authored_values(authored).items():
        values[key] = value
        sources[key] = AUTHOR

    # LAYER 1: the installer's own block, on their machine.
    #
    # NO GATE ON THE DAEMON. An agent's own settings decide how that agent runs — see
    # `overrides_daemon` for why the switch that used to sit here is gone. A stored
    # `override_default` from before is simply not a knob (the whitelist drops it), so an old
    # config keeps working.
    editable = user_may_edit(authored)
    entry = agent_entry(config, agent_id)
    for key, value in entry.items():
        # OVERRIDABLE_KEYS is a whitelist, so `override_default` and anything the user put in
        # this block by hand simply is not a knob — it never reaches the run.
        if key not in OVERRIDABLE_KEYS:
            continue
        # LOCKED: the author decided this one and did not open it up. Skipped rather than
        # refused, because a stale entry from before the agent was locked is not an error the
        # person running it can do anything about — and honouring it would let an agent that
        # says it is locked quietly run on somebody else's value.
        if sources[key] == AUTHOR and not editable:
            continue
        values[key] = value
        sources[key] = AGENT
    return values, sources
