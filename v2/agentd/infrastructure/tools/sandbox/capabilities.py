"""DefaultCapabilityResolver — the conservative, NON-interactive grant used until approval exists.

It hands an untrusted tool the minimum a normal run needs and nothing more:
  * fs   -> only the current run's workspace (so plugin output lands where the agent expects it),
  * net  -> nothing,
  * secrets -> {} ALWAYS (default-deny; the sandbox stays blind to platform keys),
  * limits -> a wall-clock timeout only.

This is the seam the approval/consent layer replaces later: an ApprovalCapabilityResolver will
implement the same `resolve()` by consulting user consent + a plugin's requested caps + policy.
Swapping it changes WHAT is granted; the sandbox that ENFORCES the grant does not change.
"""

from __future__ import annotations

from agentd.application.run_context import RunContext
from agentd.domain.sandbox import CapabilityGrant, PluginOrigin


class DefaultCapabilityResolver:
    name = "default"

    def __init__(self, timeout_s: float = 120.0) -> None:
        self._timeout_s = timeout_s

    def resolve(
        self,
        plugin_id: str,
        origin: PluginOrigin,
        ctx: RunContext | None,
    ) -> CapabilityGrant:
        workspace = ctx.workspace if (ctx is not None and ctx.workspace) else ""
        fs_paths = (workspace,) if workspace else ()
        # secrets deliberately omitted => {} (default-deny). No network. Workspace-only FS.
        return CapabilityGrant(fs_paths=fs_paths, timeout_s=self._timeout_s)
