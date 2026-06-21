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


_current: contextvars.ContextVar = contextvars.ContextVar("agentd_run_context", default=None)


def set_run_context(ctx: RunContext) -> None:
    _current.set(ctx)


def current_run_context() -> RunContext | None:
    return _current.get()


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


def take_run_outcome() -> tuple[str, str] | None:
    """Read + clear the declared outcome for the current run (None if none declared)."""
    val = _outcome.get()
    _outcome.set(None)
    return val
