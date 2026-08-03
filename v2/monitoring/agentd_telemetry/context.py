"""Ambient request context — the tracking number, carried without threading it everywhere.

Every metric and log line should carry run_id / account_id / agent_id so one search answers
"what happened to this person?". The alternative — passing an ID through several hundred
function signatures — is the reason most codebases never get correlation right.

A contextvar propagates through `await` and through asyncio tasks automatically, so binding
once at the top of a request covers everything underneath it.

LIMIT: contextvars do NOT cross a process boundary. MCP tools run as subprocesses and
sandboxed plugins may too, so those call sites must pass the ID explicitly (plan item 0.4).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_ctx: ContextVar[dict] = ContextVar("agentd_telemetry_ctx", default={})


def get() -> dict:
    return dict(_ctx.get())


def bind(**fields) -> object:
    """Add fields to the ambient context. Returns a token for `unbind`."""
    merged = dict(_ctx.get())
    merged.update({k: v for k, v in fields.items() if v is not None})
    return _ctx.set(merged)


def unbind(token) -> None:
    try:
        _ctx.reset(token)
    except (ValueError, LookupError):  # reset from a different context — harmless
        pass


@contextmanager
def scope(**fields):
    """`with scope(run_id=...):` — bind for the duration of a block."""
    token = bind(**fields)
    try:
        yield
    finally:
        unbind(token)
