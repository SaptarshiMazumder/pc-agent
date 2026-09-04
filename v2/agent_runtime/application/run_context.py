"""RunContext — the agent/session a turn is running for, available to tools.

Tools are shared, context-free objects, but a few (e.g. `cron`) need to know which
agent is calling them so a scheduled task belongs to the right agent. The
AgentService sets this per turn; a tool reads it via `current_run_context()`. It's a
contextvar, so it's task-local — concurrent runs never see each other's context.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass(frozen=True)
class RunContext:
    agent_id: str
    session_key: str
    mode: str
    workspace: str = ""  # the agent's working dir for file/exec tools ("" = use the global default)
    # Per-agent model overrides from agent.toml [plugins.*]: {plugin: {"model": ..., "tools": {tool: {"model": ...}}}}.
    # Layered ABOVE global config.plugins by resolve_tool_model. None/empty = inherit global.
    plugins: dict | None = None
    # The run's tracking number, carried EXPLICITLY because contextvars do not survive a
    # process boundary. Anything handed a RunContext — today the in-process plugin sandbox,
    # tomorrow a container/microVM/remote backend — can therefore re-establish correlation on
    # the far side instead of its work appearing as an orphan.
    run_id: str = ""
    turn_id: str = ""
    # WHERE THIS AGENT MAY WRITE. Absolute paths, already resolved — an agent.toml declares
    # `[tools.fs] write_roots` with tokens like <agents_dir>, and the service expands them
    # before they get here, so the fs tools do plain containment and know nothing about config.
    #
    # EMPTY = UNRESTRICTED, which is every agent that does not declare any. This exists for the
    # one agent whose job is authoring OTHER agents: it needs to write outside its own workspace
    # by definition, and "outside its workspace" was previously the whole filesystem — including
    # the shared plugins/ dir, whose tools are never sandboxed on the machine that installs them.
    write_roots: tuple[str, ...] = ()
    # Carved OUT of the roots. Deny beats allow. Chiefly so an agent cannot rewrite its own
    # definition, skill or allow-list — the constraints it is running under.
    write_denies: tuple[str, ...] = ()
    # Agent directories that arrived in a .agentpkg. A PLATFORM rule, not this agent's
    # declaration — editing an installed agent makes it stop matching what its publisher
    # shipped, and its signature and provenance record then describe something that no longer
    # exists on disk. Kept apart from write_denies so the refusal can say which it was.
    protected_paths: tuple[str, ...] = ()
    # THE TENANT SCOPE — what this run may SEE (read_roots) and the outer boundary its writes
    # must stay inside (write_clamp), assembled per run by user_state.tenant_scope from who is
    # calling and which deployment this is. The PLATFORM's boundary, unlike write_roots/denies
    # above, which are the agent's own declaration — an agent cannot widen these.
    #
    # EMPTY = UNRESTRICTED: a desktop run (local or cloud — one human, their machine) carries
    # (), () and behaves exactly as it always has. A hosted run carries the caller's roots, so
    # other tenants' subtrees simply do not exist for it. Enforced by write_scope.check_read /
    # check_write, the same choke point every fs path already flows through.
    read_roots: tuple[str, ...] = ()
    write_clamp: tuple[str, ...] = ()
    # WHICH ORG this turn is ATTRIBUTED to for funding ("" = personal, every desktop and every
    # non-org turn). Set where the RunContext is built, from the resolved agent's OWNER — an
    # org's agent bills the org's pool; nothing else does. A field rather than a lookup because
    # the one consumer (the model proxy's outbound trace) is infrastructure that must not
    # reach back into the registry per call, and because contextvars don't cross the process
    # boundary any better for this id than for run_id above.
    org_id: str = ""
    # The KEYS this agent declared under agent.toml [[settings]] — names only, never values.
    # Carried here because the two places that resolve a `${NAME}` placeholder (the direct
    # `fetch` and the sandbox's host-side broker) are infrastructure with no way to reach the
    # registry, and the answer is per-run anyway: the same name means this agent's private
    # credential for one agent and the machine-wide variable for another. See
    # domain.agent.resolve_setting_env for the rule.
    settings: tuple[str, ...] = ()
    # The author's DEFAULTS for those keys, from agent.toml `[[settings]] default`. Non-secret
    # only — the validator refuses a default on a secret, because a value that ships to every
    # installer is not a secret. Layered UNDER the account's stored value: an author saying
    # "start on this model" is a starting point, not an override of what the user chose.
    setting_defaults: dict[str, str] | None = None


_current: contextvars.ContextVar = contextvars.ContextVar("agentd_run_context", default=None)

#: How a setting VALUE is fetched for the CALLER. Injected by the composition root, because
#: application may not import infrastructure and "who is calling" is a connection fact. Left
#: None in tests and boot-time callers — where the answer is correctly "nothing stored, use
#: the author's default".
_settings_reader = None


def set_account_settings_reader(reader) -> None:
    """:param reader: ``(agent_id) -> {KEY: value}`` for whoever is calling right now. The
    ACCOUNT is not a parameter: it lives in a contextvar the infrastructure side already pins
    per connection, and threading it through every call site would be a second source of truth
    for the same fact."""
    global _settings_reader
    _settings_reader = reader

# The run's correlation ids, kept HERE rather than read from the telemetry library, because
# application may not import infrastructure (see v2/.importlinter). Presentation sets these
# where it starts a run; this layer only reads them. Stdlib only — no new dependency, and the
# ids are still available with telemetry uninstalled.
_trace: contextvars.ContextVar = contextvars.ContextVar("agentd_trace_ids", default=("", ""))


def set_trace_ids(run_id: str = "", turn_id: str = "") -> None:
    _trace.set((run_id or "", turn_id or ""))


def current_trace_ids() -> tuple[str, str]:
    return _trace.get()


def set_run_context(ctx: RunContext) -> None:
    _current.set(ctx)


def current_run_context() -> RunContext | None:
    return _current.get()


def current_workspace(default: str | None = None) -> str | None:
    """The working dir for the CURRENT run's file/exec tools.

    Tools are shared singletons built with the global config, so they call this to
    honour per-agent isolation: returns the agent's per-run workspace when set, else
    ``default`` (the global config workspace). Empty/unset -> ``default`` keeps `main`
    on the legacy shared workspace (back-compat).
    """
    ctx = _current.get()
    if ctx is not None and ctx.workspace:
        return ctx.workspace
    return default


def current_setting_env(name: str) -> str:
    """Which env var `${name}` reads for the CURRENT run.

    The one entry point both credential-substitution sites use — `net.outbound._resolved` on the
    author's own machine and the sandbox fetch broker when the plugin is boxed in. They have to
    agree: a plugin that works unsandboxed and 401s sandboxed (or the reverse) is the kind of bug
    that gets blamed on the sandbox for a week.

    Outside a run there is no agent, so nothing is declared and the bare name is correct — that is
    a channel or a boot-time caller reading a machine-wide variable, not an agent's private one.
    """
    from agent_runtime.domain.agent import resolve_setting_env

    ctx = _current.get()
    if ctx is None:
        return name
    return resolve_setting_env(name, ctx.agent_id, ctx.settings)


def current_setting_value(name: str) -> str:
    """The VALUE `${name}` resolves to for the current run and the current caller.

    THE ONE PLACE A SETTING IS READ. Every `${NAME}` substitution site goes through this —
    the direct `fetch`, the sandbox's host-side broker, the MCP connector's env/headers/command
    — so a plugin cannot work for one of them and 401 on another.

    THREE LAYERS, in order, and no silent step past the first two:

      1. what THIS ACCOUNT stored      — the user's own value, per tenant
      2. what the AUTHOR declared      — `[[settings]] default`, non-secret, a starting point
      3. this agent's PREFIXED env var — `<agent-id>__NAME`, the pre-account storage
      4. the machine-wide variable     — ONLY for a name this agent never declared

    Layer 3 is back-compat and a transport, not a second store: it is how a value already
    reaches a sandbox child and an `[[mcp]]` subprocess, and it is what every desktop that
    filled in a settings page before this change is running on. It is per-AGENT, never shared
    between agents — and on a hosted daemon nothing exports it any more, so the cross-tenant
    read it used to allow is gone by there being nothing to read.

    Layer 4 is deliberately unreachable for a declared name. A declared setting whose value is
    unset reads EMPTY, never the daemon's own credential: that is the silent-wrong-account
    failure the prefix scheme was invented to stop, and it must not come back through a
    fallback. `resolve_setting_env`'s docstring makes the same argument for the name.
    """
    import os

    ctx = _current.get()
    if ctx is None or name not in (ctx.settings or ()):
        # Not this agent's setting: the machine-wide variable it has always been (a provider
        # key, an operator export) — same rule `resolve_setting_env` applies to the name.
        return os.environ.get(name, "")

    if _settings_reader is not None:
        try:
            stored = _settings_reader(ctx.agent_id) or {}
        except Exception:  # noqa: BLE001 — a broken store must not take the run down
            stored = {}
        value = str(stored.get(name) or "")
        if value:
            return value

    default = str((ctx.setting_defaults or {}).get(name) or "")
    if default:
        return default

    from agent_runtime.domain.agent import setting_env_name

    return os.environ.get(setting_env_name(ctx.agent_id, name), "")


# How a plugin asks for a LIVE OAuth token: `Authorization = "Bearer ${oauth:notion}"`. Set by the
# composition root to a `(agent_id, name) -> str` resolver; None on a build without OAuth, where
# the placeholder stays literal and the provider's 401 says so.
oauth_token_resolver = None


def current_oauth_token(name: str) -> str:
    """The access token for `${oauth:<name>}` on behalf of the running agent.

    Per agent, like everything else here: two agents declaring the same provider are two
    different sign-ins, and one borrowing the other's would be acting as the wrong person.
    """
    ctx = _current.get()
    if ctx is None or oauth_token_resolver is None:
        return ""
    return oauth_token_resolver(ctx.agent_id, name) or ""


def current_plugins() -> dict:
    """Per-agent plugin/tool model overrides for the CURRENT run (from agent.toml [plugins.*]).
    Shape: {plugin: {"model": ..., "tools": {tool: {"model": ...}}}}. Empty dict when unset.
    resolve_tool_model layers this ABOVE the global config.plugins map so a named agent can
    retarget a plugin's (or a single tool's) model without touching global config."""
    ctx = _current.get()
    return (ctx.plugins if ctx is not None and ctx.plugins else {}) or {}


# --- run outcome sink -------------------------------------------------------
# A scheduled (cron) run's agent declares how it went via the `report_outcome`
# tool. The tool writes here; the gateway reads it once the run finishes to record
# the run's real result (done/blocked/failed) in the history ledger. Contextvar =
# task-local, so concurrent runs never cross; the run task is a fresh asyncio.Task
# so each starts clean.
_outcome: contextvars.ContextVar = contextvars.ContextVar("agentd_run_outcome", default=None)


def set_run_outcome(status: str, detail: str = "") -> None:
    """Record the calling run's declared outcome (status='done'|'blocked'|'failed')."""
    _outcome.set((status, detail))


def current_run_outcome() -> tuple[str, str] | None:
    """PEEK the declared outcome without clearing it (take_run_outcome consumes it).
    Lets the service check whether the agent called report_outcome before finishing."""
    return _outcome.get()


def take_run_outcome() -> tuple[str, str] | None:
    """Read + clear the declared outcome for the current run (None if none declared)."""
    val = _outcome.get()
    _outcome.set(None)
    return val
