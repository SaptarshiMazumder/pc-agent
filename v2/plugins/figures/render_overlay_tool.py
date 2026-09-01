"""render_editable_overlay: turn a high-level annotation spec into editable SVG (and optionally a PNG).

This is the fidelity engine's front door. The agent describes WHAT it wants — labels, leader
callouts, and arrows by *style* — and gets back real, editable SVG: every label a <text>, every
arrow a <path> with the premium look (tapered fills, gradients, soft heads, glow/shadow). It never
writes path coordinates by hand. The SVG is the editable deliverable; the optional PNG (returned as
an image) lets the agent SEE and refine it, and is what compose_figure_layers lays over artwork.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.domain.messages import TextContent
from figures_common import resolve_path, render_svg_to_png, png_block
import figures_overlay as overlay


class RenderOverlayTool(Tool):
    name = "render_editable_overlay"
    description = (
        "Build an EDITABLE annotation layer as SVG from a high-level spec — no hand-written path "
        "math. Elements (drawn in order): \n"
        "  • label — text, optionally in a rounded box/pill (the callout look).\n"
        "  • annotation — boxed label + leader line + target dot in one (the standard science callout).\n"
        "  • leader — a thin label->structure line, elbow/curved, optional end dot or arrowhead "
        "(head none|standard|soft).\n"
        "  • arrow — connector. `style` = the preset: generic clean|biorender|subtle|plain (default "
        "clean = soft weighty head, NOT a thin line; biorender = tapered filled body) OR semantic "
        "flow|activation|inhibition(⊣ bar)|reversible(↔ double head)|transport(dashed)|catalysis|"
        "emphasis. Override any field: route straight|elbow|curved, body stroked|tapered, head & "
        "start_head standard|soft|bar|circle|diamond|none, gradient {from,to}, filter glow|shadow, "
        "dash. Use edges[].points from layout_flowchart as an arrow's `points`.\n"
        "  • panel — a rounded boundary box with optional title (group/region frame).\n"
        "  • node — a flowchart node: rounded box + optional embedded `icon` (image path, drawn top) + "
        "wrapped `text` + optional `step` badge. Position with layout_flowchart; connect with `arrow`s. This "
        "is the editable 'icons, not just boxes' flowchart node.\n"
        "  • dot / raw — a marker point / literal SVG escape hatch.\n"
        "Writes `out_svg` (the editable layer). If `out_png` is given, also rasterizes it (transparent "
        "by default) and returns the image so you can SEE it. Coordinates are in the artwork's pixel space."
    )
    label = "Render Overlay"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["out_svg", "width", "height", "elements"],
        "properties": {
            "out_svg": {"type": "string", "description": "Output .svg path (the editable layer)."},
            "out_png": {"type": "string", "description": "Optional .png path to also rasterize (returned as an image)."},
            "width": {"type": "integer", "description": "Canvas width px (match the artwork)."},
            "height": {"type": "integer", "description": "Canvas height px (match the artwork)."},
            "scale": {"type": "number", "description": "Optional size multiplier for fonts/strokes/dots/pads. Omit = AUTO (derived from resolution, ~1024px reference) so labels never render tiny on a 2K/4K artwork. Bump it if you want chunkier labels; lower it for finer ones."},
            "background": {"type": "string", "description": "CSS colour for an opaque overlay. Omit = transparent (for layering)."},
            "font_family": {"type": "string", "description": "CSS font stack for labels. Optional."},
            "scale": {"type": "number", "description": "PNG device scale factor (if out_png). Default 2."},
            "elements": {
                "type": "array",
                "description": "Ordered annotation elements; each has a `kind` (label|annotation|leader|arrow|panel|dot|raw) plus its fields.",
                "items": {"type": "object", "required": ["kind"], "properties": {"kind": {"type": "string"}},
                          "additionalProperties": True},
            },
        },
    }

    def __init__(self, config):
        self.config = config

    def _run(self, params: dict) -> dict:
        # Resolve any node-icon path to absolute (so a workspace-relative icon embeds correctly).
        elements = []
        for el in params["elements"]:
            if el.get("kind") == "node" and el.get("icon"):
                el = {**el, "icon": str(resolve_path(self.config, el["icon"]))}
            elements.append(el)
        spec = {
            "width": int(params["width"]),
            "height": int(params["height"]),
            "background": params.get("background"),
            "font_family": params.get("font_family", overlay.DEFAULT_FONT),
            "elements": elements,
        }
        if params.get("scale"):
            spec["scale"] = float(params["scale"])
        svg = overlay.build_svg(spec)
        out_svg = resolve_path(self.config, params["out_svg"])
        out_svg.parent.mkdir(parents=True, exist_ok=True)
        out_svg.write_text(svg, encoding="utf-8")
        result = {"svg_path": str(out_svg), "width": spec["width"], "height": spec["height"],
                  "elements": len(spec["elements"]), "png_path": None}
        if params.get("out_png"):
            out_png = resolve_path(self.config, params["out_png"])
            out_png.parent.mkdir(parents=True, exist_ok=True)
            render_svg_to_png(svg, out_png, spec["width"], spec["height"],
                              scale=float(params.get("scale", 2)),
                              background=params.get("background"))
            result["png_path"] = str(out_png)
        return result

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"render_editable_overlay failed: {e}", is_error=True)
        msg = f"Overlay -> {r['svg_path']} ({r['elements']} element(s), {r['width']}x{r['height']})."
        # deliverable: the labelled figure (SVG always; PNG too when rendered)
        deliverables = [r["svg_path"]] + ([r["png_path"]] if r["png_path"] else [])
        if r["png_path"]:
            return ToolResult(content=[TextContent(text=msg + f" PNG -> {r['png_path']}."),
                                       png_block(Path(r["png_path"]))], details=r,
                              artifacts=deliverables)
        return ToolResult.text(msg, details=r, artifacts=deliverables)
