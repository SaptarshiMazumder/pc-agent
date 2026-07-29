"""Plugin-sandbox ports — the two seams untrusted-tool execution plugs into.

Both are ``Protocol``s (structural, DIP): the daemon depends on these shapes, never on a concrete
backend, so the local passthrough today and a gVisor/Firecracker/remote backend tomorrow are
swapped without touching the engine.

  * ``PluginSandbox`` — the EXECUTION seam. Call-based (not the shell-command ``Sandbox`` port):
    ``params in -> ToolResult out`` is the ONLY thing that crosses the trust boundary. A backend
    ENFORCES the ``CapabilityGrant`` it is handed; it does not decide it.
  * ``CapabilityResolver`` — the POLICY seam that PRODUCES the grant. Today a conservative default;
    later the approval/consent layer implements this same shape. Kept deliberately separate from
    ``PluginSandbox`` so approval can be built later without changing execution.

Note the split: the resolver answers "what may this run touch?"; the sandbox answers "run it in
isolation with exactly that." Approval, when it lands, is a `CapabilityResolver` — not a sandbox.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from agentd.application.interfaces.tool import OnUpdate, ToolResult
from agentd.application.run_context import RunContext
from agentd.domain.sandbox import CapabilityGrant, PluginOrigin


@runtime_checkable
class PluginSandbox(Protocol):
    """Runs ONE untrusted plugin tool in isolation and returns its ToolResult.

    The daemon holds a plugin's tools by (plugin_id, tool_name) — it does NOT pass the live tool
    object across this boundary, because a real backend (container / microVM / remote) loads the
    plugin's code INSIDE the sandbox from the plugin package. A local/in-process backend resolves
    the same key back to a loaded tool. `grant` is enforced by the backend; `ctx` scopes the run
    (workspace, agent) but carries no host authority of its own.
    """

    name: str

    async def run_tool(
        self,
        plugin_id: str,
        tool_name: str,
        tool_call_id: str,
        params: dict,
        abort: asyncio.Event,
        on_update: OnUpdate | None = None,
        *,
        grant: CapabilityGrant,
        ctx: RunContext | None = None,
    ) -> ToolResult:
        """Execute the tool under `grant`; never raise — surface failures as an error ToolResult."""
        ...


@runtime_checkable
class CapabilityResolver(Protocol):
    """Decides the CapabilityGrant for a plugin's tool run — the seam approval plugs into later.

    Today's default implementation is non-interactive (a fixed conservative grant). A future
    ApprovalCapabilityResolver implements this SAME method by consulting user consent / stored
    grants / policy — the sandbox and everything below it are untouched by that swap.
    """

    def resolve(
        self,
        plugin_id: str,
        origin: PluginOrigin,
        ctx: RunContext | None,
    ) -> CapabilityGrant:
        """Return the rights this plugin's tool may use for this run (default-deny on doubt)."""
        ...
