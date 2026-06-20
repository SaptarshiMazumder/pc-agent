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
