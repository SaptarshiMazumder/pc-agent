"""post_start — plan a new Instagram post from a collection (the agent's model + the post playbook)."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import ToolResult
from agent_runtime.application.run_context import current_run_context

from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.post_model_tool import PostModelTool
from ad_generation.presentation.post_plan_text import PostPlanText


class PostStartTool(PostModelTool):
    name = "post_start"
    label = "Plan a post"
    plugin = PLUGIN
    description = (
        "Plan an Instagram post from a collection: the slides in order, the words on each and when "
        "they fade, how each clip is cut, the caption and hashtags — following the post playbook. "
        "Show the plan, ask the questions it returns, then post_replan / post_update with the answers."
    )
    parameters = {
        "type": "object",
        "required": ["collection"],
        "properties": {
            "collection": {"type": "string", "description": "The collection's name or slug."},
            "format": {"type": "string", "enum": ["carousel", "reel"], "description": "Default carousel."},
            "notes": {"type": "string", "description": "What the user said about the post, in their words: order, hook, what to leave out, sources."},
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            service = self._service(on_update)
            post, questions = await asyncio.to_thread(
                service.start,
                str(params.get("collection") or ""),
                str(params.get("format") or "carousel"),
                str(params.get("notes") or ""),
                str(getattr(current_run_context(), "session_key", "") or ""),
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"post_start: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(PostPlanText.of(post, questions), details={"post": post.to_dict(), "questions": questions})
