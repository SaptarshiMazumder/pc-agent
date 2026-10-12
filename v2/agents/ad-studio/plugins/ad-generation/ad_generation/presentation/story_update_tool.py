"""story_update — change a story directly: a field, one shot, a new shot, a removed shot, the
order. Free and instant (the window's edits go through here too). A new version."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.story_service import StoryService
from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.story_text import StoryText


class StoryUpdateTool(Tool):
    name = "story_update"
    label = "Change a story"
    plugin = PLUGIN
    description = (
        "Change a story directly, only what the user said: fields (title, hook, audience, offer, tone, cast, "
        "language, format, caption_angle, cta, notes, occasion, products, auto_pick); `shot` {id, and any "
        "of shows, purpose, kind, product, text, count, aspect, seconds, start_from — the user's own image path}; "
        "`add_shot` {shows, purpose, kind, …}; "
        "`remove_shot` (an id); `order` (every shot id in the new order). Free."
    )
    parameters = {
        "type": "object",
        "required": ["story"],
        "properties": {
            "story": {"type": "string"},
            "title": {"type": "string"}, "hook": {"type": "string"}, "audience": {"type": "string"},
            "goal": {"type": "string"}, "offer": {"type": "string"}, "tone": {"type": "string"},
            "language": {"type": "string"}, "format": {"type": "string", "enum": ["reel", "carousel"]},
            "cast": {"type": "string", "description": "Who is in it, as the user chose: a cast name or 'none'."},
            "caption_angle": {"type": "string"}, "cta": {"type": "string"}, "notes": {"type": "string"},
            "occasion": {"type": "string"}, "products": {"type": "array", "items": {"type": "string"}},
            "auto_pick": {"type": "boolean"},
            "collection": {"type": "string", "description": "The collection its post is made from (set by the studio)."},
            "shot": {"type": "object"}, "add_shot": {"type": "object"}, "remove_shot": {"type": "string"},
            "order": {"type": "array", "items": {"type": "string"}},
        },
    }

    def __init__(self, stories: StoryService) -> None:
        self._stories = stories

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            changes = {k: v for k, v in params.items() if k != "story"}
            if not changes:
                raise ValueError("say what to change")
            story = self._stories.update(str(params.get("story") or ""), changes)
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"story_update: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(StoryText.of(story), details={"story": story.to_dict()})
