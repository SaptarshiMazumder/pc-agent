"""Browser facade: every chat gets a private task, profile, tabs and filesystem."""
from __future__ import annotations

from agent_runtime.application.interfaces.tool import ToolResult
from agent_runtime.domain.messages import Artifact
from agent_runtime.infrastructure.tools.mcp.isolated import content_blocks
from agent_runtime.infrastructure.tools.sandbox.isolated_session import (
    IsolatedSession,
    session_owner,
)


class RemoteBrowserProvider:
    def __init__(self, config):
        self.config = config
        self.sessions = {}

    def session(self):
        owner = session_owner()
        if owner not in self.sessions:
            self.sessions[owner] = IsolatedSession(self.config, "browser")
        return self.sessions[owner]

    async def remote_tool(self, params):
        from pathlib import Path

        from agent_runtime.application.write_scope import check_read, check_write

        for path in params.get("paths", []) or []:
            check_read(Path(path))
        if params.get("path"):
            check_write(Path(params["path"]))
        result = await self.session().call("browser", params)
        return ToolResult(content=content_blocks(result.get("content", [])),
                          details=result.get("details"), is_error=result.get("is_error", False),
                          artifacts=[Artifact(**item) for item in result.get("artifacts", [])])

    async def remote_fetch(self, url, max_chars):
        return await self.session().call("fetch", {"url": url, "max_chars": max_chars})

    async def close(self):
        for session in self.sessions.values():
            await session.close()
        self.sessions.clear()
