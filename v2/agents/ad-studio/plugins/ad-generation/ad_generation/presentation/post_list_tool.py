"""post_list — the posts, one post, or the one a chat is making. Read only."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.infrastructure.run_workspace import RunWorkspace
from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.tool_progress_reporter import ToolProgressReporter


class PostListTool(Tool):
    name = "post_list"
    label = "List the posts"
    plugin = PLUGIN
    description = "List the posts, or read one (`post`), or the one this chat is making (`session`). Read only."
    parameters = {
        "type": "object",
        "properties": {"post": {"type": "string"}, "session": {"type": "string", "description": "A chat's session key."}},
    }

    def __init__(self, service_factory, workspace: RunWorkspace) -> None:
        self._factory = service_factory
        self._ws = workspace

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            service = self._factory(None, ToolProgressReporter(asyncio.get_running_loop(), on_update))
            if params.get("post"):
                posts = [service.get(str(params["post"]))]
            elif params.get("session"):
                one = service.for_session(str(params["session"]))
                posts = [one] if one else []
            else:
                posts = service.all()
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"post_list: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(
            "\n".join(f"{p.slug}: {p.name} ({p.format}, {len(p.slides)} slides{', rendered' if p.rendered else ''})" for p in posts)
            or "no posts yet",
            details={
                "root": str(self._ws.root()),
                "posts": [p.to_dict() for p in posts],
                # where each post's last design run is — the window shows it while it moves
                "designing": {p.slug: d.to_dict() for p in posts if (d := service.design_progress(p.slug))},
            },
        )
