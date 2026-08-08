"""ReloadAgentTool — the `reload_agent` tool surface.

A thin adapter over ReloadAgentService. NOT retryable: it mutates live process state (the agent
registry and the tool catalog), so an automatic retry on a slow call could re-register mid-flight.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult


class ReloadAgentTool(Tool):
    name = "reload_agent"
    label = "Reload Agent"
    default_retryable = False  # mutates live registry/catalog state
    description = (
        "Make a newly authored or edited AGENT take effect without a restart: re-reads its "
        "agent.toml, picks up any NEW tools in its plugins/ folder, and tells every connected "
        "client so the agent appears in the sidebar. Call it after creating an agent or editing "
        "its agent.toml. NOT needed for skills or ui/ — a SKILL.md is re-read every turn and ui/ "
        "is served straight off disk, so those are already live the moment you save them."
    )
    parameters = {
        "type": "object",
        "required": ["agent_id"],
        "properties": {
            "agent_id": {"type": "string", "description": "the agent to reload (e.g. my-agent)"}
        },
    }

    def __init__(self, service):
        self._service = service

    async def execute(self, tool_call_id, params, abort, on_update=None):
        message, is_error = self._service.reload(params.get("agent_id", ""))
        return ToolResult.text(message, is_error=is_error)
