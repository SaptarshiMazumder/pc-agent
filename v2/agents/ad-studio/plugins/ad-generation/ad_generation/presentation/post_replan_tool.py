"""post_replan — plan a post again with the user's answers and wishes."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import ToolResult

from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.post_model_tool import PostModelTool
from ad_generation.presentation.post_plan_text import PostPlanText


class PostReplanTool(PostModelTool):
    name = "post_replan"
    label = "Plan a post again"
    plugin = PLUGIN
    description = (
        "Plan a WHOLE post again with the user's answers and wishes (`notes`, in their words) — e.g. where each "
        "product was found, a new hook, another order. Every slide is planned anew: a slide whose picture or "
        "words change loses its design. For one precise change (a caption, a hashtag, one slide's words) use "
        "post_update instead."
    )
    parameters = {
        "type": "object",
        "required": ["post", "notes"],
        "properties": {"post": {"type": "string"}, "notes": {"type": "string"}},
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            service = self._service(on_update)
            post, questions = await asyncio.to_thread(service.replan, str(params.get("post") or ""), str(params.get("notes") or ""))
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"post_replan: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(PostPlanText.of(post, questions), details={"post": post.to_dict(), "questions": questions})
