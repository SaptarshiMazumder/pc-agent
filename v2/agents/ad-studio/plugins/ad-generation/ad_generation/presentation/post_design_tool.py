"""post_design — design a post's slides like a studio would: the agent's model writes each slide as
HTML/CSS (layout, type, colour, shapes, the photos placed, the clip's slot, CSS motion), from
scratch or adapted from a template, following design references from the library; each is
rendered and reviewed by an art-director pass, fixed, and shown as a preview. Model calls only —
no image generation."""

from __future__ import annotations

import asyncio
import threading

from agent_runtime.application.interfaces.tool import ToolResult

from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.post_model_tool import PostModelTool


class PostDesignTool(PostModelTool):
    name = "post_design"
    label = "Design a post's slides"
    plugin = PLUGIN
    default_timeout_sec = 1800.0
    description = (
        "Design slides of a planned post as professional Instagram ads — layout, type, colour blocks, "
        "shapes, the photos placed, text over clips with motion — following the design playbook. "
        "`slides`: the slide numbers (default all); `template`: a template's slug to start from "
        "(design_template_list); `references`: reference slugs to follow (design_reference_list) — leave "
        "out to let it choose from the whole library, [] for none; `notes`: what the user wants, in their "
        "words ('cleaner', 'the price bigger', 'a collage for slide 2'). Each slide is rendered and "
        "reviewed; previews come back."
    )
    parameters = {
        "type": "object",
        "required": ["post"],
        "properties": {
            "post": {"type": "string"},
            "slides": {"type": "array", "items": {"type": "integer"}, "description": "Slide numbers, 1-based; leave out for all."},
            "template": {"type": "string", "description": "A template's slug to adapt; leave out to design freely."},
            "references": {"type": "array", "items": {"type": "string"}, "description": "Reference slugs to follow; leave out to choose from the library, [] for none."},
            "notes": {"type": "string"},
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        # A thread cannot be killed: Stop (the job cancelled, or `abort`) sets this, and the design
        # run checks it before every model call — so stopping stops the spending.
        stop = threading.Event()
        try:
            service = self._service(on_update)
            results = await asyncio.to_thread(
                service.design, str(params.get("post") or ""), [int(n) for n in params.get("slides") or []],
                str(params.get("notes") or ""), str(params.get("template") or ""),
                [str(s) for s in params["references"]] if params.get("references") is not None else None,
                lambda: stop.is_set() or abort.is_set(),
            )
        except asyncio.CancelledError:
            stop.set()
            raise
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"post_design: {type(e).__name__}: {e}", is_error=True)
        lines = [
            f"slide {r['slide']}: {r['preview']}" + (f" (after reference {r['reference']})" if r["reference"] else "") + (f" — still to fix: {'; '.join(r['still_to_fix'])}" if r["still_to_fix"] else " — passed review")
            for r in results
        ]
        return ToolResult.text("\n".join(lines) + "\nRender the post (post_render) when the user is happy.",
                               details={"designed": results}, artifacts=[r["preview"] for r in results])
