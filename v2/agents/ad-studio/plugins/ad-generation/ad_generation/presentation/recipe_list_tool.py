"""recipe_list — the recipes an ad can start from, for the window's new-ad screen and Recipes page.
Read only."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.campaign_view_service import CampaignViewService
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class RecipeListTool(Tool):
    name = "recipe_list"
    label = "List the recipes"
    plugin = PLUGIN
    description = "List the recipes: key, title, what each is for, its steps, and whether it casts a model. Read only."
    parameters = {"type": "object", "properties": {}}

    def __init__(self, views: CampaignViewService) -> None:
        self._views = views

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            recipes = self._views.recipes()
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"recipe_list: {type(e).__name__}: {e}", is_error=True)
        lines = [f"{r['key']} — {' → '.join(s['title'] for s in r['steps'])}" for r in recipes]
        return ToolResult.text("\n".join(lines), details={"recipes": recipes})
