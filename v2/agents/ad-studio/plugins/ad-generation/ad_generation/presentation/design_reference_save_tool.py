"""design_reference_save — keep a picture of a design worth following (a Canva template's preview,
a post seen elsewhere, the user's screenshot) in the reference library; the agent's model reads off
it the layout, photo shapes, type, palette, decoration and what it suits. One vision call."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import ToolResult

from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.post_model_tool import PostModelTool


class DesignReferenceSaveTool(PostModelTool):
    name = "design_reference_save"
    label = "Save a design reference"
    plugin = PLUGIN
    description = (
        "Keep a picture of ONE design worth following in the reference library: `image` (a workspace path — "
        "a browser screenshot saved with `to`, or an upload), `crop` [x, y, width, height] in its pixels to "
        "keep one design out of a page of them, `name`, `source` (the link it came from, or 'your "
        "screenshot'), `notes` (what the user likes about it). Its structure is read and kept; designs follow "
        "it with the post's own pictures and words."
    )
    parameters = {
        "type": "object",
        "required": ["image"],
        "properties": {
            "image": {"type": "string"},
            "crop": {"type": "array", "items": {"type": "integer"}, "description": "[x, y, width, height] in the picture's pixels"},
            "name": {"type": "string"},
            "source": {"type": "string"},
            "notes": {"type": "string"},
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            service = self._service(on_update)
            r = await asyncio.to_thread(
                service.add, str(params.get("image") or ""), str(params.get("name") or ""), str(params.get("source") or ""),
                list(params["crop"]) if params.get("crop") else None, str(params.get("notes") or ""),
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"design_reference_save: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(
            f"reference '{r.slug}' kept — suits {', '.join(r.suits) or '—'}; layout: {r.spec.get('layout', '')}",
            details={"reference": r.to_dict()}, artifacts=[r.image],
        )
