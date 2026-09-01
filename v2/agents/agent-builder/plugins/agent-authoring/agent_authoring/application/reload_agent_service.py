"""ReloadAgentService — the use case: "make what was just written actually take effect".

Thin by design. The adapter does the three concrete steps (refresh the definition, re-scan
private plugins, announce); this service owns only the guard clauses and turns the adapter's
raw result into the sentence the model reads.
"""

from __future__ import annotations


class ReloadAgentService:
    def __init__(self, reloader):
        self._reloader = reloader

    def reload(self, agent_id: str) -> tuple[str, bool]:
        """Returns ``(message, is_error)`` — the tool layer only wraps it."""
        agent_id = (agent_id or "").strip()
        if not agent_id:
            return "reload_agent needs an `agent_id`", True

        result = self._reloader.reload(agent_id)
        error = result.get("error")

        if not result.get("definition"):
            return f"could not reload '{agent_id}': {error or 'the registry refresh failed'}", True

        # The refresh succeeded but this agent is not in the roster — it does not exist (or its
        # agent.toml is unreadable, so the registry skipped it). Reporting "reloaded" here would
        # be a lie the model then builds on.
        if not result.get("found"):
            return (
                f"no agent '{agent_id}' after reloading the roster — check the id, and that "
                f"agents/{agent_id}/agent.toml exists and parses",
                True,
            )

        parts = [f"Reloaded '{agent_id}': definition re-read from agent.toml"]
        tools = result.get("tools")
        if tools:
            parts.append(f"{tools} private tool(s) loaded from its plugins/")
        elif tools == 0:
            parts.append("no private tools (none declared, or none loadable)")
        # SAY WHETHER THERE IS MCP TO CHECK. The line above reports PRIVATE tools, and an agent
        # whose tools all come from a declared server used to read "no private tools" — which is
        # true, unrelated, and was taken as "the daemon is not even trying the MCP servers".
        mcp = result.get("mcp")
        if mcp:
            parts.append(
                f"{mcp} declared MCP server(s) will re-dial on its next run — call "
                f"mcp_status to see what they expose and why any of them did not come up"
            )
        parts.append("clients notified" if result.get("announced") else "clients NOT notified")

        message = " — ".join(parts) + "."
        if error:
            # The definition IS live; a later step degraded. Report it without failing the turn.
            return f"{message} Note: {error}", False
        return message, False
