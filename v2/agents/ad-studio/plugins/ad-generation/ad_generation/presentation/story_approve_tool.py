"""story_approve — the user's go on a story: its shot list is final for this version, and making can
start. Refused, naming them, while counts, shapes or lengths are unchosen (unless the story's Auto
is on). Free."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.story_service import StoryService
from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.story_text import StoryText


class StoryApproveTool(Tool):
    name = "story_approve"
    label = "Approve a story"
    plugin = PLUGIN
    description = "Approve a story — ONLY when the user said it is good (or the window's Approve). Free."
    parameters = {"type": "object", "required": ["story"], "properties": {"story": {"type": "string"}}}

    def __init__(self, stories: StoryService) -> None:
        self._stories = stories

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            story = self._stories.approve(str(params.get("story") or ""))
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"story_approve: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(StoryText.of(story), details={"story": story.to_dict()})
