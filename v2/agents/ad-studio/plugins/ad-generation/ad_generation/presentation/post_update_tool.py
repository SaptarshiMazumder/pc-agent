"""post_update — the user's own changes to a post, exactly. Free; the studio calls it directly."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.tool_progress_reporter import ToolProgressReporter
from ad_generation.presentation.post_plan_text import PostPlanText


class PostUpdateTool(Tool):
    name = "post_update"
    label = "Change a post"
    plugin = PLUGIN
    description = (
        "Change a post exactly as the user said: `slides` (every slide, in order, as post_list returns "
        "them — their words, timing, clip cuts), `caption`, `hashtags`, `format`, `name`. Free; render again after."
    )
    parameters = {
        "type": "object",
        "required": ["post"],
        "properties": {
            "post": {"type": "string"},
            "slides": {"type": "array", "items": {"type": "object"}},
            "caption": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "format": {"type": "string", "enum": ["carousel", "reel"]},
            "name": {"type": "string"},
        },
    }

    def __init__(self, service_factory) -> None:
        self._factory = service_factory

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            service = self._factory(None, ToolProgressReporter(asyncio.get_running_loop(), on_update))
            post = service.update(str(params.get("post") or ""), {k: v for k, v in params.items() if k != "post"})
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"post_update: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(PostPlanText.of(post, []), details={"post": post.to_dict()})
