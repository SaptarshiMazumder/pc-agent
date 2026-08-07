"""DefaultCapabilityResolver — the conservative, NON-interactive grant used until approval exists.

It hands an untrusted tool the minimum a normal run needs and nothing more:
  * fs   -> only the current run's workspace (so plugin output lands where the agent expects it),
  * net  -> nothing,
  * secrets -> {} ALWAYS (default-deny; the sandbox stays blind to platform keys),
  * limits -> wall-clock, and CPU/memory where the platform can enforce them.

This is the seam the approval/consent layer replaces later: an ApprovalCapabilityResolver will
implement the same `resolve()` by consulting user consent + a plugin's requested caps + policy.
Swapping it changes WHAT is granted; the sandbox that ENFORCES the grant does not change.

The limits come from `config.sandbox_limits` rather than being fixed here, because the right
ceiling is a property of the deployment: a hosted task sharing CPU across accounts wants a tight
one, and a desktop running a plugin that renders video for two minutes wants a loose one. The
defaults below are the loose end — a limit that breaks legitimate work gets switched off wholesale,
which costs more than it saves.
"""

from __future__ import annotations

from agent_runtime.application.run_context import RunContext
from agent_runtime.domain.sandbox import CapabilityGrant, PluginOrigin

DEFAULT_TIMEOUT_S = 120.0


class DefaultCapabilityResolver:
    name = "default"

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S, config=None) -> None:
        limits = dict(getattr(config, "sandbox_limits", None) or {}) if config is not None else {}
        self._timeout_s = float(limits.get("timeout_s") or timeout_s)
        self._cpu_ms = int(limits.get("cpu_ms") or 0)
        self._mem_mb = int(limits.get("mem_mb") or 0)

    def resolve(
        self,
        plugin_id: str,
        origin: PluginOrigin,
        ctx: RunContext | None,
    ) -> CapabilityGrant:
        workspace = ctx.workspace if (ctx is not None and ctx.workspace) else ""
        fs_paths = (workspace,) if workspace else ()
        # secrets deliberately omitted => {} (default-deny). No network. Workspace-only FS.
        return CapabilityGrant(
            fs_paths=fs_paths,
            timeout_s=self._timeout_s,
            cpu_ms=self._cpu_ms,
            mem_mb=self._mem_mb,
        )
