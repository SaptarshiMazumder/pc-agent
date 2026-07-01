"""figexport plugin — editable / publication figure exports.

  • export_pptx — artwork + REAL text boxes + connector arrows -> editable PowerPoint (python-pptx).
  • export_pdf  — SVG -> VECTOR PDF via headless Chromium (selectable text, scalable).

(Layered editable SVG export already lives in figures.compose_layers; PNG in figures.render_svg /
compose_layers — so figexport only adds the two formats those don't cover.) No new dependency:
python-pptx is already installed and the PDF path reuses the existing browser.
"""

from __future__ import annotations

from export_pptx_tool import ExportPptxTool
from export_pdf_tool import ExportPdfTool


def register(api, ctx):
    api.register_tool(ExportPptxTool(ctx.config))
    api.register_tool(ExportPdfTool(ctx.config))
