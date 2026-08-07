"""Which PluginSandbox backend this deployment runs — the ONE place a backend is named.

Config-driven, never hardcoded: `config.sandbox_plugin_backend` (env `AGENTD_SANDBOX_BACKEND`)
picks by name, and an unknown name falls back to the safest thing that still works rather than
crashing the daemon on a typo — with a warning, because a silent downgrade of a security control
is worse than a noisy one.

  local       in-process passthrough. No isolation. The desktop default: one person, their own
              machine, their own plugins — a process per tool call would be pure cost.
  subprocess  a child process per call. The hosted default, and what `multi_tenant` turns on.

A gVisor/Firecracker/remote backend is added here and nowhere else.
"""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")

LOCAL = "local"
SUBPROCESS = "subprocess"
BACKENDS = (LOCAL, SUBPROCESS)


def resolve_backend_name(config) -> str:
    """The backend this config asks for, normalised.

    Empty means "decide for me", and the decision is the deployment's shape: a daemon serving many
    accounts isolates by default, a desktop daemon does not. Making that implicit is deliberate —
    the alternative is a hosted deployment that is one forgotten env var away from running
    marketplace code in-process next to everyone's files.
    """
    raw = str(getattr(config, "sandbox_plugin_backend", "") or "").strip().lower()
    if not raw:
        return SUBPROCESS if getattr(config, "multi_tenant", False) else LOCAL
    if raw not in BACKENDS:
        log.warning(
            "sandbox: unknown backend %r (known: %s) — using %s",
            raw, ", ".join(BACKENDS), SUBPROCESS,
        )
        return SUBPROCESS
    return raw


def build_plugin_sandbox(config):
    """-> the PluginSandbox this deployment should use."""
    from .local import LocalPluginSandbox
    from .subprocess_backend import SubprocessPluginSandbox

    name = resolve_backend_name(config)
    if name == SUBPROCESS:
        log.info("sandbox: untrusted plugin tools run in a SUBPROCESS (one process per call)")
        return SubprocessPluginSandbox(config)
    return LocalPluginSandbox()
