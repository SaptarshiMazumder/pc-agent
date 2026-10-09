"""post_attach — a post designed in Canva: the pages the agent downloaded from the browser become
the post's files, in order, with the caption and a zip — once each page is checked (vision) to be
its slide: that slide's photo and words. A design that is not the plan is refused, page by page."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import ToolResult

from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.post_model_tool import PostModelTool


class PostAttachTool(PostModelTool):
    name = "post_attach"
    label = "Attach a Canva design to a post"
    plugin = PLUGIN
    description = (
        "After designing a post in Canva (browser) and downloading its pages: `files` — the downloaded "
        "workspace paths, in slide order (a Canva .zip of pages is opened, pages in order) — become the "
        "post's files, with the caption and a zip. Each page is checked to show its slide's photo and the "
        "plan's words; a design that is not the plan is refused, page by page. Give the design's Canva "
        "link as `canva_url`. Report what the check says — never more."
    )
    parameters = {
        "type": "object",
        "required": ["post", "files"],
        "properties": {
            "post": {"type": "string"},
            "files": {"type": "array", "items": {"type": "string"}},
            "canva_url": {"type": "string", "description": "The Canva design's link (its edit URL)."},
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            service = self._service(on_update)
            made = await asyncio.to_thread(
                service.attach, str(params.get("post") or ""), [str(f) for f in params.get("files") or []],
                str(params.get("canva_url") or ""),
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"post_attach: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(
            "checked:\n" + "\n".join(made["checked"]) + "\nfiles:\n" + "\n".join(made["files"])
            + f"\nzip: {made['zip']}\ncaption:\n{made['caption']}",
            details=made,
            artifacts=[*made["files"], made["zip"]],
        )
