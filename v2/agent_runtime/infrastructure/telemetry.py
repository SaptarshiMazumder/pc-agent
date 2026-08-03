"""Daemon-side access to the shared telemetry library (v2/monitoring).

One import shim so the optional-dependency dance lives in ONE place instead of being repeated
at every call site. When `agentd_telemetry` is absent — an older desktop install, a wheel built
before the package existed — every function here is a no-op and the daemon behaves exactly as
it did before. A metrics package must never be the reason an agent fails to run.

    from agent_runtime.infrastructure import telemetry
    telemetry.bind(run_id=..., agent_id=...)
    with telemetry.timer("tool_ms", source="plugin", _props={"tool": name}):
        ...

Why `bind` and not a `with` block in most places: the daemon runs each turn in its own
asyncio task, and create_task snapshots the current context — so binding at the top of a run
is already isolated to that run. Same reasoning the accounts contextvar pin uses.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext

try:
    from agentd_telemetry import (  # noqa: F401
        bind,
        count,
        gauge,
        get,
        money,
        scope,
        setup_logging,
        timer,
        timing,
    )

    AVAILABLE = True
except ImportError:  # pragma: no cover — exercised only on installs without the package
    AVAILABLE = False

    def bind(**_fields):  # type: ignore[misc]
        return None

    def get() -> dict:  # type: ignore[misc]
        return {}

    def count(*_a, **_k) -> None:  # type: ignore[misc]
        pass

    def timing(*_a, **_k) -> None:  # type: ignore[misc]
        pass

    def gauge(*_a, **_k) -> None:  # type: ignore[misc]
        pass

    def money(*_a, **_k) -> None:  # type: ignore[misc]
        pass

    def setup_logging(*_a, **_k) -> None:  # type: ignore[misc]
        pass

    def scope(**_k):  # type: ignore[misc]
        return nullcontext()

    @contextmanager
    def timer(*_a, **_k):  # type: ignore[misc]
        yield


def trace_headers() -> dict:
    """The correlation IDs as outbound HTTP headers.

    This is how the tracking number crosses from our process into the Model Proxy's. The proxy
    reads these back, stamps them on its own log lines, and forwards them to the usage ledger —
    which is what makes one ID answer "what happened, and what did it cost?" across three
    services that share no database.
    """
    ctx = get()
    headers = {}
    if ctx.get("run_id"):
        headers["X-Agentd-Run-Id"] = str(ctx["run_id"])
    if ctx.get("turn_id"):
        headers["X-Agentd-Turn-Id"] = str(ctx["turn_id"])
    if ctx.get("agent_id"):
        headers["X-Agentd-Agent-Id"] = str(ctx["agent_id"])
    return headers
