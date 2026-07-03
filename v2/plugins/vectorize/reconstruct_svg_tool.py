"""reconstruct_svg: turn a FLAT scientific image into an editable layered SVG by semantically
rebuilding its labels and arrows as real vector objects (the "AI Vectorizer").

Unlike dumb tracing (which turns a label into letter-shaped outlines), this asks a vision model to
READ every text label and detect every arrow, then rebuilds them as live <text>/<path> via the
figures overlay engine, layered over the original artwork (embedded as <image>). So you can retype a
label or recolour an arrow.

LIMITATION (honest): the editable vector layer sits ON TOP of the original raster, whose baked-in
text still shows underneath. For a clean swap you'd inpaint/remove the original text first (or
generate fresh art with generate_artwork) and use this layer over that. Returns the discovered
elements so you can also feed them into render_overlay yourself.
"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

from agentd.application.interfaces.tool import Tool, ToolResult
from agentd.application.run_context import current_workspace
from agentd.application.tool_models import resolve_tool_model
from agentd.domain.messages import TextContent

import vectorize_gemini as vg

_PROMPT = """This is a {w}x{h} px scientific figure (origin top-left). Identify EVERY visible text
label and EVERY arrow/connector so they can be rebuilt as editable vector objects.

Return ONLY JSON: {{"elements": [ ... ]}} where each element is one of:
  {{"kind":"label","text":"<verbatim>","x":<int>,"y":<int>,"anchor":"start","font_size":<int>,"color":"#rrggbb"}}
  {{"kind":"arrow","points":[[x1,y1],[x2,y2]],"route":"straight","color":"#rrggbb","head":"standard"}}
Use absolute pixels within {w}x{h}. Transcribe text VERBATIM (exact spelling/case). Only clearly
visible elements. x,y for a label = the START (left baseline-ish) of its text."""


class ReconstructSvgTool(Tool):
    name = "reconstruct_svg"
    plugin = "vectorize"
    needs_model = True
    default_model = vg.DEFAULT_MODEL   # config plugins.vectorize.* overrides
    description = (
        "Semantically VECTORIZE a flat image: a vision model reads its text labels and detects its "
        "arrows, then rebuilds them as EDITABLE <text>/<path> layered over the original artwork "
        "(embedded as <image>) -> an editable .svg. Input: `image` (path), `out_svg`. Returns the "
        "discovered `elements` (render_overlay-compatible) too. NOTE: the original baked text remains "
        "in the raster underneath — for a clean swap, vectorize over text-removed/fresh artwork. "
        "Uses Gemini (GEMINI_API_KEY/GOOGLE_API_KEY)."
    )
    label = "Reconstruct SVG"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["image", "out_svg"],
        "properties": {
            "image": {"type": "string", "description": "Path to the flat image to vectorize."},
            "out_svg": {"type": "string", "description": "Output layered editable .svg path."},
            "model": {"type": "string", "description": f"Override the vision model (resolves per-call > config plugins.vectorize.tools.reconstruct_svg > plugins.vectorize default > {vg.DEFAULT_MODEL})."},
            "api_key": {"type": "string", "description": "Override key (else GEMINI_API_KEY/GOOGLE_API_KEY)."},
        },
    }

    def __init__(self, config):
        self.config = config

    def _resolve(self, p: str) -> Path:
        path = Path(p)
        if path.is_absolute():
            return path
        ws = current_workspace(str(getattr(self.config, "workspace", "."))) or "."
        return Path(ws) / path

    def _overlay_engine(self):
        """Import the figures plugin's overlay engine from the sibling plugin dir (explicit, so it
        doesn't depend on plugin load order)."""
        figures_dir = str(Path(__file__).resolve().parent.parent / "figures")
        if figures_dir not in sys.path:
            sys.path.insert(0, figures_dir)
        import figures_overlay
        return figures_overlay

    def _run(self, params: dict) -> dict:
        img = self._resolve(params["image"])
        w, h = vg.image_dims(img)
        key = vg.resolve_key(params.get("api_key"), self.config)
        model = resolve_tool_model(self.config, self.plugin, self.name,
                                   per_call=params.get("model"), default=self.default_model)
        data = vg.analyze_json(img, _PROMPT.format(w=w, h=h), model=model, api_key=key)
        elements = data.get("elements", data if isinstance(data, list) else [])
        overlay = self._overlay_engine()
        defs, body, _, _ = overlay.build_inner({"width": w, "height": h, "elements": elements})
        mime = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
        b64 = base64.b64encode(img.read_bytes()).decode()
        layered = (
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">{defs}'
            f'<image x="0" y="0" width="{w}" height="{h}" preserveAspectRatio="none" '
            f'xlink:href="data:{mime};base64,{b64}"/>{body}</svg>')
        out = self._resolve(params["out_svg"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(layered, encoding="utf-8")
        n_labels = sum(1 for e in elements if e.get("kind") == "label")
        n_arrows = sum(1 for e in elements if e.get("kind") == "arrow")
        return {"out_svg": str(out), "width": w, "height": h, "elements": elements,
                "labels": n_labels, "arrows": n_arrows}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"reconstruct_svg failed: {e}", is_error=True)
        return ToolResult.text(
            f"Reconstructed {r['labels']} label(s) + {r['arrows']} arrow(s) as editable vector over "
            f"the artwork -> {r['out_svg']} ({r['width']}x{r['height']}).", details=r)
