"""export_pptx: build an editable PowerPoint figure — artwork picture + REAL text boxes + connector
arrows, from the same render_overlay-style `elements` the figure pipeline already produces.

This is the "editable in PowerPoint" deliverable: each label becomes a text box you can retype, each
arrow becomes a connector you can drag/recolour. The slide is sized to the artwork's pixels (96 dpi),
so element pixel coordinates land exactly on the picture.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentd.application.interfaces.tool import Tool, ToolResult
from figexport_common import resolve_path, px


def _add_arrowhead(connector):
    """python-pptx has no arrowhead API — set <a:tailEnd type="triangle"/> on the line XML.
    Cosmetic, so never let it break the export."""
    try:
        from pptx.oxml.ns import qn
        ln = connector.line._get_or_add_ln()
        tail = ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
        ln.append(tail)
    except Exception:
        pass


class ExportPptxTool(Tool):
    name = "export_pptx"
    description = (
        "Export an editable PowerPoint (.pptx) figure: the artwork as a picture, plus REAL editable "
        "text boxes and connector arrows built from render_overlay-style `elements` (label / "
        "annotation / arrow). Slide is sized to the artwork's pixels so coordinates line up. Input: "
        "`artwork` (PNG/JPG), `elements`, `out_path`. Labels become retypable text boxes; arrows "
        "become draggable connectors."
    )
    label = "Export PPTX"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["artwork", "out_path", "elements"],
        "properties": {
            "artwork": {"type": "string", "description": "Path to the raster artwork (PNG/JPG)."},
            "out_path": {"type": "string", "description": "Output .pptx path."},
            "elements": {
                "type": "array",
                "description": "render_overlay-style elements: label {text,x,y,anchor,font_size,color}, "
                               "annotation {text,at[x,y],target[x,y]}, arrow {points:[[x,y],..],color}.",
                "items": {"type": "object", "required": ["kind"], "properties": {"kind": {"type": "string"}},
                          "additionalProperties": True},
            },
        },
    }

    def __init__(self, config):
        self.config = config

    def _resolve(self, p):
        return resolve_path(self.config, p)

    def _textbox(self, slide, text, cx, cy, font_size, color, anchor):
        from pptx.util import Pt, Emu
        from pptx.dml.color import RGBColor
        fs = float(font_size or 14)
        w_px = max(20, len(str(text)) * fs * 0.62)
        h_px = fs * 1.6
        left = cx - (w_px / 2 if anchor == "middle" else (w_px if anchor == "end" else 0))
        tb = slide.shapes.add_textbox(Emu(px(left)), Emu(px(cy - h_px / 2)), Emu(px(w_px)), Emu(px(h_px)))
        tf = tb.text_frame
        tf.word_wrap = False
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        r = tf.paragraphs[0].add_run()
        r.text = str(text)
        r.font.size = Pt(fs * 0.75)
        r.font.bold = True
        try:
            r.font.color.rgb = RGBColor.from_string(str(color or "#1f2937").lstrip("#"))
        except Exception:
            pass
        return tb

    def _connector(self, slide, p0, p1, color, head=True):
        from pptx.enum.shapes import MSO_CONNECTOR
        from pptx.util import Emu, Pt
        from pptx.dml.color import RGBColor
        c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,
                                       Emu(px(p0[0])), Emu(px(p0[1])), Emu(px(p1[0])), Emu(px(p1[1])))
        try:
            c.line.color.rgb = RGBColor.from_string(str(color or "#374151").lstrip("#"))
        except Exception:
            pass
        c.line.width = Pt(2)
        if head:
            _add_arrowhead(c)
        return c

    def _run(self, params: dict) -> dict:
        from pptx import Presentation
        from pptx.util import Emu
        from PIL import Image

        art = self._resolve(params["artwork"])
        with Image.open(art) as im:
            W, H = im.size
        prs = Presentation()
        prs.slide_width = Emu(px(W))
        prs.slide_height = Emu(px(H))
        slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
        slide.shapes.add_picture(str(art), Emu(0), Emu(0), width=Emu(px(W)), height=Emu(px(H)))

        n_text = n_arrow = 0
        for el in params["elements"]:
            kind = el.get("kind")
            if kind == "label":
                self._textbox(slide, el.get("text", ""), float(el.get("x", 0)), float(el.get("y", 0)),
                              el.get("font_size", 14), el.get("color"), el.get("anchor", "start"))
                n_text += 1
            elif kind == "annotation":
                at = el.get("at", [0, 0]); tgt = el.get("target", at)
                self._textbox(slide, el.get("text", ""), float(at[0]), float(at[1]),
                              el.get("font_size", 14), el.get("color"), "middle")
                self._connector(slide, at, tgt, el.get("leader_color", "#9ca3af"), head=False)
                n_text += 1; n_arrow += 1
            elif kind == "arrow":
                pts = el.get("points") or [el.get("from", [0, 0]), el.get("to", [0, 0])]
                self._connector(slide, pts[0], pts[-1], el.get("color", "#374151"),
                                head=(el.get("head", "standard") != "none"))
                n_arrow += 1
            # panels/dots/raw are decorative -> skipped in the editable export
        out = self._resolve(params["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        prs.save(str(out))
        return {"out_path": str(out), "width": W, "height": H, "textboxes": n_text, "connectors": n_arrow}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"export_pptx failed: {e}", is_error=True)
        return ToolResult.text(
            f"PPTX -> {r['out_path']} ({r['textboxes']} editable text box(es), "
            f"{r['connectors']} connector(s); slide {r['width']}x{r['height']}px).", details=r)
