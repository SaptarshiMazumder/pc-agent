"""post_render — the files to upload to Instagram, the caption and a zip. No model runs, nothing is paid."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.tool_progress_reporter import ToolProgressReporter


class PostRenderTool(Tool):
    name = "post_render"
    label = "Render a post"
    plugin = PLUGIN
    default_timeout_sec = 900.0
    description = (
        "Render a post into the files to upload to Instagram, in order (full-bleed slides with their "
        "text; clips cut, faded and with their text animated; or one Reel), plus the caption and a zip. "
        "No model runs — nothing is paid."
    )
    parameters = {"type": "object", "required": ["post"], "properties": {"post": {"type": "string"}}}

    def __init__(self, service_factory) -> None:
        self._factory = service_factory

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            service = self._factory(None, ToolProgressReporter(asyncio.get_running_loop(), on_update))
            made = await asyncio.to_thread(service.render, str(params.get("post") or ""))
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"post_render: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(
            "\n".join(made["files"]) + f"\nzip: {made['zip']}\ncaption:\n{made['caption']}",
            details=made,
            artifacts=[*made["files"], made["zip"]],
        )
