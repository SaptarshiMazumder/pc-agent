"""vectorize plugin — geometric tracing of an existing flat image into vector shapes.

  • trace_image — GEOMETRIC: color regions -> Bezier paths (logos / clean line-art). Needs a vtracer
    backend; errors actionably if absent.

NOTE: making a labelled figure's TEXT + ARROWS editable is NOT done here — that's `read_labels_from_image`
(vision plugin) → render_editable_overlay → compose_figure_layers over a clean textless base. The old semantic
"reconstruct_svg" vectorizer was removed: it painted white over detected text to avoid double labels,
which white-outs the artwork when labels aren't purely on a white margin.
"""

from __future__ import annotations

from trace_image_tool import TraceImageTool


def register(api, ctx):
    api.register_tool(TraceImageTool(ctx.config))
