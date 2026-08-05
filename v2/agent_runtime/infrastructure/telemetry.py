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
        configure_upload,
        count,
        flush_upload,
        gauge,
        get,
        money,
        scope,
        setup_logging,
        timer,
        timing,
        upload_status,
    )

    AVAILABLE = True
except ImportError:  # pragma: no cover — exercised only on installs without the package
    AVAILABLE = False

    def configure_upload(**_k) -> None:  # type: ignore[misc]
        pass

    def flush_upload() -> int:  # type: ignore[misc]
        return 0

    def upload_status() -> dict:  # type: ignore[misc]
        return {"enabled": False, "active": False, "url": ""}

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


def pin_telemetry_stream(stream):
    """Point the telemetry library's own output at `stream` (None = normal sys.stdout).

    Used only by the plugin stdout capture, which redirects `sys.stdout` process-wide and would
    otherwise swallow our metrics along with the plugin's prints. A no-op — returning None — when
    the library is absent or predates `pin_stream`, since there is nothing to protect then.
    """
    try:
        from agentd_telemetry import emf

        return emf.pin_stream(stream)
    except (ImportError, AttributeError):  # pragma: no cover
        return None


def resolve_ingest_url(config) -> str:
    """Where diagnostics would go, if enabled. Env wins, then config, then the flavor.

    Same precedence as every other platform URL. The flavor's value is the one that matters in
    production: a shipped build knows its own environment, and a user should never have to type
    an endpoint to send us a bug report.
    """
    import os

    env = (os.environ.get("AGENTD_INGEST_URL", "") or "").strip()
    if env:
        return env.rstrip("/")
    explicit = str(getattr(config, "ingest_url", "") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    distribution = getattr(config, "distribution", None)
    return str(getattr(distribution, "ingest_url", "") or "").strip().rstrip("/")


def apply_diagnostics(config, *, token: str | None = None) -> dict:
    """Push the CURRENT consent + endpoint into the uploader. Returns its status.

    ONE function, called from the three places the answer can change — boot, a settings save, and
    sign-in/out — so the uploader can never be left running on a stale answer. In particular:
    turning the toggle off must stop the sending immediately, not at the next restart, or the
    "off" the user just clicked is a lie for as long as the daemon happens to stay up.
    """
    url = resolve_ingest_url(config)
    enabled = bool(getattr(config, "diagnostics_upload", False)) and bool(url)
    configure_upload(enabled=enabled, url=url, token=token, surface="desktop")
    return upload_status()


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
