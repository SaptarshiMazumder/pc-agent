"""figures plugin — the deterministic fidelity core for scientific figures.

Five tools, each named for its specific job (no "do everything" tool):
  • render_svg      — rasterize SVG -> PNG (browser fidelity) and hand the image back to SEE it.
  • validate_svg    — parse + inventory an SVG (labels/arrows/images): the structural-correctness check.
  • layout_flowchart     — node/edge layout + connector routing -> waypoints for arrows.
  • render_editable_overlay  — high-level annotation spec -> EDITABLE SVG (labels, leaders, premium arrows).
  • compose_figure_layers  — raster artwork + vector overlay -> flattened PNG and/or layered editable SVG.

(The old `arrange_labels` — which re-spread labels into a generic ring and threw away the model's own
placement, making results worse — was removed. Labels keep the position the image model gave them.)

All pure-Python + Playwright + Pillow — no new binaries, so the plugin loads anywhere the html plugin does.
"""

from __future__ import annotations

from render_svg_tool import RenderSvgTool
from validate_svg_tool import ValidateSvgTool
from route_graph_tool import RouteGraphTool
from render_overlay_tool import RenderOverlayTool
from compose_layers_tool import ComposeLayersTool


def register(api, ctx):
    api.register_tool(RenderSvgTool(ctx.config))
    api.register_tool(ValidateSvgTool(ctx.config))
    api.register_tool(RouteGraphTool(ctx.config))
    api.register_tool(RenderOverlayTool(ctx.config))
    api.register_tool(ComposeLayersTool(ctx.config))
