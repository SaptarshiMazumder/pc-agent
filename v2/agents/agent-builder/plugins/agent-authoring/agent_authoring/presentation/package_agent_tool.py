"""PackageAgentTool — the `package_agent` tool surface.

A thin adapter: params in, service call, ToolResult out. The service decides everything; this
layer only names the parameters and hands back the text.

Not retryable: it writes an artifact. Re-running is harmless in practice (same inputs produce
the same file name, overwritten), but an automatic retry on a timeout would be re-zipping a
potentially large tree for no reason.
"""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult


class PackageAgentTool(Tool):
    name = "package_agent"
    label = "Package Agent"
    default_retryable = False  # writes an artifact
    description = (
        "Package an agent into a shareable .agentpkg — the installable unit another person "
        "downloads, and the input to building a standalone .exe. Zips the whole agents/<id>/ "
        "directory (definition, prose, skills, ui/, and its own plugins/) plus any shared plugins "
        "its bundle.toml vendors. VALIDATES FIRST and refuses to package an agent with errors, so "
        "a broken agent never reaches someone else's machine. The bundle's version comes from "
        "`version` in agent.toml — bump it before packaging a change, because installs supersede "
        "BY VERSION and re-packing the same number will not replace an existing copy. Call this "
        "last, after validate_agent and reload_agent."
    )
    parameters = {
        "type": "object",
        "required": ["agent_id"],
        "properties": {
            "agent_id": {"type": "string", "description": "the agent to package (e.g. my-agent)"},
            "out_dir": {
                "type": "string",
                "description": "where to write the .agentpkg (default: <state_dir>/dist)",
            },
            "version": {
                "type": "string",
                "description": "override the bundle version for this build; normally omit it and "
                "let agent.toml's `version` decide",
            },
        },
    }

    def __init__(self, service):
        self._service = service

    async def execute(self, tool_call_id, params, abort, on_update=None):
        # OFF THE EVENT LOOP. The daemon is single-threaded — one thread carries every
        # websocket, every model stream, every agent's run and every tool. Zipping an agent takes seconds, and longer for a big one, and a
        # plain call here stopped the ENTIRE daemon for that long: no events, no replies,
        # not even an answer to the client's keepalive. Windows concluded the connection
        # was dead, reconnected, and told people "the daemon restarted mid-run" when it
        # had done nothing of the sort.
        #
        # A thread is the whole fix, and `verify_app` already does this for the same
        # reason: one blocking operation with nothing to interleave.
        result = await asyncio.to_thread(
            self._service.package,
            params.get("agent_id", ""),
            params.get("out_dir", ""),
            params.get("version", ""),
        )
        return ToolResult.text(
            result.message,
            is_error=not result.ok,
            details={
                "ok": result.ok,
                "path": result.path,
                "sha256": result.sha256,
                "size": result.size,
                "bundleId": result.bundle_id,
                "version": result.version,
                "privatePlugins": list(result.private_plugins),
            },
        )
