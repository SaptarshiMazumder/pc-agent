"""story_list — one story (`story`), or every story newest first. Free."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.story_service import StoryService
from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.story_text import StoryText


class StoryListTool(Tool):
    name = "story_list"
    label = "List stories"
    plugin = PLUGIN
    description = "Read one story in full (`story`), or list every story (no arguments)."
    parameters = {"type": "object", "properties": {"story": {"type": "string"}}}

    def __init__(self, stories: StoryService) -> None:
        self._stories = stories

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            if params.get("story"):
                story = self._stories.get(str(params["story"]))
                return ToolResult.text(StoryText.of(story), details={"story": story.to_dict()})
            stories = self._stories.all()
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"story_list: {type(e).__name__}: {e}", is_error=True)
        lines = [f"{s.id} · v{s.version} {s.status} · {s.title} · {len(s.shots)} shots" for s in stories] or ["no stories yet"]
        return ToolResult.text("\n".join(lines), details={"stories": [s.to_dict() for s in stories]})
