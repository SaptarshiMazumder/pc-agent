"""figures plugin — the deterministic fidelity core for scientific figures.

Six tools, each named for its specific job (no "do everything" tool):
  • render_svg      — rasterize SVG -> PNG (browser fidelity) and hand the image back to SEE it.
  • validate_svg    — parse + inventory an SVG (labels/arrows/images): the structural-correctness check.
  • route_graph     — node/edge layout + connector routing -> waypoints for arrows.
  • place_labels    — anchor points -> non-overlapping, edge-aligned label callouts (render_overlay-ready).
  • render_overlay  — high-level annotation spec -> EDITABLE SVG (labels, leaders, premium arrows).
  • compose_layers  — raster artwork + vector overlay -> flattened PNG and/or layered editable SVG.

These cover the *hybrid* figure path end to end EXCEPT the artwork generation and the vision-based
anchor extraction (separate plugins, built next). All pure-Python + Playwright + Pillow — no new
binaries, so the plugin loads anywhere the html plugin already does.
"""

from __future__ import annotations

from render_svg_tool import RenderSvgTool
from validate_svg_tool import ValidateSvgTool
from route_graph_tool import RouteGraphTool
from place_labels_tool import PlaceLabelsTool
from render_overlay_tool import RenderOverlayTool
from compose_layers_tool import ComposeLayersTool


def register(api, ctx):
    api.register_tool(RenderSvgTool(ctx.config))
    api.register_tool(ValidateSvgTool(ctx.config))
    api.register_tool(RouteGraphTool(ctx.config))
    api.register_tool(PlaceLabelsTool(ctx.config))
    api.register_tool(RenderOverlayTool(ctx.config))
    api.register_tool(ComposeLayersTool(ctx.config))
