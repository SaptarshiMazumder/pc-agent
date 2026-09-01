"""LocalPluginSandbox — the default PluginSandbox backend: in-process passthrough.

It proves the trust boundary (``params in -> ToolResult out`` crosses one seam) WITHOUT changing
behaviour: it looks the inner tool up by (plugin_id, tool_name) and calls its real ``execute``, on
the host, in-process. There is NO real isolation here and it does NOT enforce ``grant`` — that is
exactly what the blue backends (gVisor container / Firecracker microVM / remote) add later behind
this same port. Shipping this first keeps first-party dev working and lets the whole seam — wrapper,
classifier, resolver, port — be wired and tested before any infra exists.

Registration mirrors a real backend's "load the plugin inside the sandbox": here the load is a dict
lookup; there it is importing the plugin package within the isolated runtime.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import OnUpdate, Tool, ToolResult
from agent_runtime.application.run_context import RunContext
from agent_runtime.domain.sandbox import CapabilityGrant
from agent_runtime.infrastructure.tools.sandbox import stdout_capture


class LocalPluginSandbox:
    name = "local"

    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], Tool] = {}

    def register(self, plugin_id: str, tool_name: str, tool: Tool) -> None:
        """Make the inner tool resolvable by (plugin_id, tool_name). Called by SandboxedTool."""
        self._tools[(plugin_id, tool_name)] = tool

    async def run_tool(
        self,
        plugin_id: str,
        tool_name: str,
        tool_call_id: str,
        params: dict,
        abort,
        on_update: OnUpdate | None = None,
        *,
        grant: CapabilityGrant,
        ctx: RunContext | None = None,
    ) -> ToolResult:
        tool = self._tools.get((plugin_id, tool_name))
        if tool is None:
            return ToolResult.text(
                f"sandbox: tool '{tool_name}' from plugin '{plugin_id}' is not loaded",
                is_error=True,
            )
        # Passthrough. A future backend runs this inside an isolated runtime and enforces `grant`.
        #
        # The ONE thing this backend does enforce is stdout (5.4). An in-process plugin's `print`
        # goes to the same stdout as our EMF lines, so without this its output reaches CloudWatch
        # raw — outside the redaction allowlist entirely, because the allowlist was never in that
        # path. Captured, scrubbed, capped and re-emitted tagged with the plugin id.
        if not stdout_capture.enabled():
            return await tool.execute(tool_call_id, params, abort, on_update)
        with stdout_capture.capture(plugin_id, tool_name):
            return await tool.execute(tool_call_id, params, abort, on_update)
