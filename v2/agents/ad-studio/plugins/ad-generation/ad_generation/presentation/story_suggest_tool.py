"""story_suggest — the occasions coming up and the story themes that suit them and the picked
products, best first. Free (no model call)."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.story_service import StoryService
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class StorySuggestTool(Tool):
    name = "story_suggest"
    label = "Suggest stories"
    plugin = PLUGIN
    description = (
        "The occasions coming up (posting now first) and the story themes that suit them and the "
        "products, best first — to offer the user 2-3 stories. `products`: shop product keys (optional)."
    )
    parameters = {
        "type": "object",
        "properties": {"products": {"type": "array", "items": {"type": "string"}, "description": "Shop product keys."}},
    }

    def __init__(self, stories: StoryService) -> None:
        self._stories = stories

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            found = self._stories.suggest([str(p) for p in params.get("products") or []])
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"story_suggest: {type(e).__name__}: {e}", is_error=True)
        occ = [f"{o['name']} {o['next']}" + (" (posting now)" if o["posting_now"] else "") for o in found["occasions"][:6]]
        themes = [f"{t['key']} — {t['name']}: {t['summary']} [{t['why_now']}]" for t in found["themes"][:8]]
        text = f"today {found['today']}\noccasions: " + "; ".join(occ) + "\nthemes:\n" + "\n".join(themes)
        return ToolResult.text(text, details=found)
