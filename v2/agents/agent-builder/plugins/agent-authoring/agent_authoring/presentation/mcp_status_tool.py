"""McpStatusTool — the `mcp_status` tool surface: what an agent's declared servers ACTUALLY did.

THE BLIND SPOT THIS EXISTS TO CLOSE. Agent Builder could write an `[[mcp]]` block and had no way
whatsoever to find out whether it worked. `reload_agent` reports private plugins and says nothing
about MCP; `run_agent` reports the tools the model called, which for a server that never came up
is indistinguishable from a model that chose not to call anything. The `connect-mcp` skill told it
to "call mcp.status and read back the tool names" — but that is a GATEWAY method, reachable from an
app window and not from a tool call, so the instruction named an action the model could not take.

The cost was measured: twenty-one minutes of an agent shelling out `uvx --help`, reading approval
files and re-editing config, to rediscover a reason the daemon had already written down.

IT DIALS, it does not just read. ``ensure`` is lazy by design — a declared server connects on the
agent's first run — so a freshly authored agent has no cached state at all, and reporting that
would say "no problems" about an agent with no tools. Calling ``ensure`` first is what makes the
answer true at the moment it is asked.

READ-ONLY, and deliberately so: it changes no files and writes no config. Its whole job is to turn
"the agent says it cannot do the thing" into a reason with a fix attached.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult


class McpStatusTool(Tool):
    name = "mcp_status"
    label = "MCP Status"
    description = (
        "Report the DECLARED [[mcp]] servers of an agent you are building: for each one its "
        "transport, the exact command or URL, the ${...} settings it still needs, the tools it "
        "actually exposed, and the reason it is not up. Call this whenever an agent you built "
        "does not have the tools you expected — it is the only way to see why, and it beats "
        "guessing at the package or the config. Connects any server that has not been tried yet, "
        "so it is also the way to confirm a new [[mcp]] block works before you tell the user it "
        "does."
    )
    parameters = {
        "type": "object",
        "required": ["agent_id"],
        "properties": {
            "agent_id": {"type": "string", "description": "the agent to inspect (e.g. my-agent)"}
        },
    }

    def __init__(self, registry, connector):
        self._registry = registry
        self._connector = connector

    async def execute(self, tool_call_id, params, abort, on_update=None):
        agent_id = str(params.get("agent_id") or "").strip()
        if not agent_id:
            return ToolResult.text("mcp_status needs an `agent_id`", is_error=True)
        if self._registry is None or self._connector is None:
            return ToolResult.text(
                "this daemon has no declared-MCP support, so there is nothing to report",
                is_error=True,
            )
        try:
            spec = self._registry.get(agent_id)
        except Exception as e:  # noqa: BLE001 — a roster failure is a message, not a crashed turn
            return ToolResult.text(
                f"could not read agent '{agent_id}': {type(e).__name__}: {e}", is_error=True
            )
        if spec is None:
            return ToolResult.text(f"no agent '{agent_id}' — check the id", is_error=True)

        declarations = tuple(getattr(spec, "mcp", ()) or ())
        if not declarations:
            return ToolResult.text(
                f"'{agent_id}' declares no [[mcp]] servers. If it should reach a third-party "
                f"service, declare one in its agent.toml — see the connect-mcp skill."
            )

        # DIAL FIRST. Without this a never-run agent reports nothing wrong and no tools, which
        # reads as "fine" and is the exact confusion this tool exists to end.
        await self._connector.ensure(spec)

        problems = self._connector.problems_for(agent_id)
        live = self._connector.tools_for(agent_id)

        lines: list[str] = []
        for decl in declarations:
            where = " ".join(decl.command) if decl.transport == "stdio" else (decl.url or "")
            tools = sorted(
                t.name for t in live if str(getattr(t, "name", "")).startswith(f"{decl.name}__")
            )
            problem = problems.get(decl.name, "")
            mark = "[x]" if problem else "[ok]"
            lines.append(f"{mark} {decl.name}  ({decl.transport})  {where}")
            if decl.placeholders:
                lines.append(f"      needs settings: {', '.join(decl.placeholders)}")
            if problem:
                lines.append(f"      PROBLEM: {problem}")
            if tools:
                lines.append(f"      {len(tools)} tool(s): {', '.join(tools)}")
            elif not problem:
                # Connected and empty is its own failure, and it looks identical to success from
                # every other angle — the agent simply has nothing to call.
                lines.append("      no tools exposed — the server started and offered nothing")

        blocked = [n for n, why in problems.items() if why]
        header = (
            f"'{agent_id}': {len(declarations) - len(blocked)}/{len(declarations)} declared "
            f"server(s) up."
        )
        footer = ""
        if blocked:
            footer = (
                "\n\nA server that 'needs <SETTING>' is waiting on the USER to fill that field in "
                "on the agent's settings page — say so plainly rather than editing config. Any "
                "other reason is yours to fix: check the command and its arguments against the "
                "server's own documentation before changing anything else."
            )
        return ToolResult.text("\n".join([header, "", *lines]) + footer)
