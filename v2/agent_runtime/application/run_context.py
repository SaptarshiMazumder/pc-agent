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


_current: contextvars.ContextVar = contextvars.ContextVar("agentd_run_context", default=None)

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
