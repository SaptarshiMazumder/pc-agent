"""Which PluginSandbox backend this deployment runs — the ONE place a backend is named.

Config-driven, never hardcoded: `config.sandbox_plugin_backend` (env `AGENTD_SANDBOX_BACKEND`)
picks by name, and an unknown name falls back to the safest thing that still works rather than
crashing the daemon on a typo — with a warning, because a silent downgrade of a security control
is worse than a noisy one.

  local       in-process passthrough. No isolation. The fallback for a host that CANNOT launch a
              child process (a Windows daemon stuck on the Selector event loop).
  subprocess  a child process per call. The default WHEREVER the host can spawn one — desktop and
              hosted alike, because third-party code is third-party whether one person or a
              thousand are served.
  microvm     the call leaves the box entirely: the executor service runs the same worker inside
              a Firecracker microVM (one Lambda invocation per call). The hosted rule — untrusted
              code never executes on the machine holding every tenant's data — is this backend.
              Needs AGENTD_EXECUTOR_URL; a microvm daemon without one fails untrusted calls
              CLOSED rather than quietly running them locally.

Deciding by HOST CAPABILITY, not deployment shape, is deliberate: a marketplace agent's tools rode
in inside someone else's package, so a desktop that can isolate them should, exactly as the hosted
daemon does. A gVisor/Firecracker/remote backend is added here and nowhere else.
"""

from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger("agentd")

LOCAL = "local"
SUBPROCESS = "subprocess"
MICROVM = "microvm"
BACKENDS = (LOCAL, SUBPROCESS, MICROVM)


def host_can_spawn_subprocess() -> bool:
    """Can this host launch the child process the subprocess backend needs?

    POSIX always can. On Windows only the Proactor event loop can spawn a subprocess (the Selector
    loop raises NotImplementedError); the daemon entrypoint sets the Proactor policy before any loop
    is created (see main.py), so this is normally True there too. PROBED rather than assumed so a
    host that somehow runs on the Selector loop degrades to in-process WITH A WARNING instead of
    failing every untrusted tool call with a confusing spawn error.
    """
    if os.name != "nt":
        return True
    policy = asyncio.get_event_loop_policy()
    proactor = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    return proactor is not None and isinstance(policy, proactor)


def resolve_backend_name(config) -> str:
    """The backend this config asks for, normalised.

    A named backend is honoured as-is. Empty means "decide for me", and the decision is the HOST'S
    CAPABILITY, not the deployment's shape: run the strongest isolation this machine can actually
    provide. Third-party plugin code is untrusted whether one person or a thousand are served, so a
    desktop that can spawn a subprocess isolates it exactly as the hosted daemon does. It falls back
    to the in-process passthrough ONLY where a real child process cannot be launched — with a
    warning, because that means downloaded code runs with this daemon's own access.
    """
    raw = str(getattr(config, "sandbox_plugin_backend", "") or "").strip().lower()
    if raw:
        if raw in BACKENDS:
            return raw
        log.warning(
            "sandbox: unknown backend %r (known: %s) — deciding by host capability instead",
            raw, ", ".join(BACKENDS),
        )
    if host_can_spawn_subprocess():
        return SUBPROCESS
    log.warning(
        "sandbox: this host cannot launch a subprocess sandbox (Windows Selector event loop) — "
        "untrusted plugins will run IN-PROCESS. Start the daemon on the Proactor loop to isolate "
        "them (main.py sets it; a custom embedder may not)."
    )
    return LOCAL


def build_plugin_sandbox(config):
    """-> the PluginSandbox this deployment should use."""
    from .local import LocalPluginSandbox
    from .subprocess_backend import SubprocessPluginSandbox

    name = resolve_backend_name(config)
    if name == MICROVM:
        from .microvm_backend import MicrovmPluginSandbox

        if not str(getattr(config, "executor_url", "") or "").strip():
            # Configured for microVMs but no executor to send work to. The backend itself fails
            # every call closed with the same message — building it anyway (rather than falling
            # back to subprocess) is the point: a deployment that asked for off-box isolation
            # must never silently get on-box isolation instead.
            log.warning(
                "sandbox: AGENTD_SANDBOX_BACKEND=microvm but AGENTD_EXECUTOR_URL is empty — "
                "every untrusted tool call will fail closed until it is set"
            )
        log.info("sandbox: untrusted plugin tools run OFF-BOX (one microVM per call)")
        return MicrovmPluginSandbox(config)
    if name == SUBPROCESS:
        log.info("sandbox: untrusted plugin tools run in a SUBPROCESS (one process per call)")
        return SubprocessPluginSandbox(config)
    return LocalPluginSandbox()
