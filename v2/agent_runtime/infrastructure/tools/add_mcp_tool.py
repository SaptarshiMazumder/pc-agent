"""add_mcp — connect an MCP server by chatting and load its tools live (B2).

A thin agent-facing wrapper over the gateway's existing ``mcp.add`` machinery (``_mcp_add``):
it connects the server, merges its tools into the live catalog (no restart), and persists it to
the config. This is the "download/wire an MCP" path — config + connect, no code written. The
connect/persist logic is injected as a callable so this tool stays out of the presentation layer.
"""

from __future__ import annotations

from . import Tool, ToolResult


class AddMcpTool(Tool):
    name = "add_mcp"
    label = "Add MCP"
    concurrency = "sequential"  # mutates the shared catalog / config
    default_retryable = False
    description = (
        "Connect an MCP server and load its tools LIVE (no restart), then remember it. Give a "
        '`name` and EITHER a stdio `command` (argv list, e.g. ["npx","-y","@scope/server"]) '
        "OR an http `url`. Optional `env` (for the command) and `headers` (for the url). Use this "
        "to add a whole toolset from an existing MCP server; the new tools appear next turn under "
        "the server's namespace. (Connecting an external server runs/reaches third-party code — "
        "only add servers you trust.)"
    )
    parameters = {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "description": "a short id for the server (its tool namespace)",
            },
            "command": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'stdio launch argv, e.g. ["npx","-y","@scope/mcp"]',
            },
            "url": {"type": "string", "description": "http(s) endpoint (use instead of command)"},
            "env": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "env vars for the stdio command (e.g. API keys)",
            },
            "headers": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "http headers for the url (e.g. Authorization)",
            },
        },
    }

    def __init__(self, add_fn):
        self._add = add_fn  # async (params: dict) -> dict  (the gateway's _mcp_add)

    async def execute(self, tool_call_id, params, abort, on_update=None):
        name = (params.get("name") or "").strip()
        if not name:
            return ToolResult.text("add_mcp needs a 'name'", is_error=True)
        if not (params.get("command") or params.get("url")):
            return ToolResult.text(
                "add_mcp needs a 'command' (stdio) or 'url' (http)", is_error=True
            )
        try:
            result = await self._add(
                {
                    "name": name,
                    "command": params.get("command"),
                    "url": params.get("url"),
                    "env": params.get("env"),
                    "headers": params.get("headers"),
                }
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"add_mcp failed: {type(e).__name__}: {e}", is_error=True)
        if not result.get("added"):
            return ToolResult.text(f"could not add '{name}': {result.get('error')}", is_error=True)
        tools = result.get("tools") or []
        return ToolResult.text(
            f"Added MCP server '{name}' — {len(tools)} tool(s) now available: "
            + ", ".join(tools[:20])
            + (" …" if len(tools) > 20 else ""),
            details=result,
        )
