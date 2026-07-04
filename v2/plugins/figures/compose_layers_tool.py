"""compose_figure_layers: lay an editable vector overlay over artwork — raster OR fully-vector.

Two artwork modes:
  • RASTER artwork (`artwork` = PNG/JPG): the layered SVG embeds it as one <image>; the painted
    structures stay pixels (scalable as a whole, not individually). The default hybrid figure.
  • VECTOR artwork (`artwork_svg` / `artwork_svg_path`, e.g. from trace_image): the layered SVG nests
    the traced artwork as live paths, so EVERY artwork shape is an editable vector too — the
    FigureLabs "AI Vectorizer" style, all-vector output (no embedded raster).

Either way the overlay (labels/leaders/arrows) is live <text>/<path>. Outputs (either/both):
  • out_png — flattened composite (add `scale` for hi-res).
  • out_svg — the editable deliverable (layered raster+overlay, or fully-vector).

The overlay and the (traced) artwork must share the artwork's pixel coordinate space — which
render_editable_overlay / layout_flowchart / trace_image all already do — so they line up 1:1.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from agentd.application.interfaces.tool import Tool, ToolResult
from agentd.domain.messages import TextContent
from figures_common import resolve_path, render_svg_to_png, png_block, svg_size

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _strip_decl(svg: str) -> str:
    s = svg.strip()
    if s.startswith("<?xml"):
        s = s[s.index("?>") + 2:].strip()
    return s


def _inner_svg(svg: str) -> str:
    """Return the INNER content (defs + body) of an <svg>, dropping its outer <svg>…</svg> wrapper.
    Inlining the overlay this way — instead of nesting a whole <svg> element — keeps the composed
    figure a FLAT svg, which Illustrator/Inkscape/other editors render reliably (many of them silently
    drop or mis-clip a nested <svg>, which is why arrows/labels could vanish outside a browser)."""
    s = _strip_decl(svg)
    open_end = s.find(">", s.find("<svg"))
    close = s.rfind("</svg>")
    if open_end == -1 or close == -1:
        return s
    return s[open_end + 1:close]


class ComposeLayersTool(Tool):
    name = "compose_figure_layers"
    description = (
        "Composite artwork with a vector overlay into the final figure. Artwork is ONE of: `artwork` "
        "(raster PNG/JPG — embedded as <image>, the default hybrid) OR `artwork_svg`/`artwork_svg_path` "
        "(vector, e.g. from trace_image — nested as live paths so EVERY artwork shape is editable too, "
        "the FigureLabs 'AI Vectorizer' all-vector style). Overlay = `overlay_svg`/`overlay_svg_path` "
        "in the artwork's pixel coords. Outputs (either/both): `out_png` (flattened, `scale` for hi-res), "
        "`out_svg` (the editable deliverable). Returns the composite PNG so you can SEE it."
    )
    label = "Compose Layers"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "properties": {
            "artwork": {"type": "string", "description": "Raster artwork path (PNG/JPG). Provide ONE artwork source."},
            "artwork_svg": {"type": "string", "description": "Raw vector artwork SVG (e.g. trace_image output) for an all-vector figure."},
            "artwork_svg_path": {"type": "string", "description": "Path to a vector artwork .svg."},
            "overlay_svg": {"type": "string", "description": "Raw overlay SVG. Provide this OR overlay_svg_path."},
            "overlay_svg_path": {"type": "string", "description": "Path to the overlay .svg."},
            "out_png": {"type": "string", "description": "Flattened composite output .png."},
            "out_svg": {"type": "string", "description": "Editable output .svg (layered raster+overlay, or all-vector)."},
            "scale": {"type": "number", "description": "Hi-res factor for out_png (1 = native size). Default 1."},
        },
    }

    def __init__(self, config):
        self.config = config

    def _overlay_text(self, params) -> str:
        if bool(params.get("overlay_svg")) == bool(params.get("overlay_svg_path")):
            raise ValueError("provide exactly ONE of: overlay_svg, overlay_svg_path")
        if params.get("overlay_svg_path"):
            return resolve_path(self.config, params["overlay_svg_path"]).read_text(encoding="utf-8")
        return params["overlay_svg"]

    def _artwork(self, params):
        """Return ('raster', Path) or ('vector', svg_text). Exactly one source allowed."""
        srcs = [k for k in ("artwork", "artwork_svg", "artwork_svg_path") if params.get(k)]
        if len(srcs) != 1:
            raise ValueError("provide exactly ONE of: artwork, artwork_svg, artwork_svg_path")
        if params.get("artwork"):
            return "raster", resolve_path(self.config, params["artwork"])
        if params.get("artwork_svg_path"):
            return "vector", resolve_path(self.config, params["artwork_svg_path"]).read_text(encoding="utf-8")
        return "vector", params["artwork_svg"]

    def _run(self, params: dict) -> dict:
        if not params.get("out_png") and not params.get("out_svg"):
            raise ValueError("provide at least one of: out_png, out_svg")
        kind, art = self._artwork(params)
        overlay_text = self._overlay_text(params)
        scale = float(params.get("scale", 1))

        if kind == "vector":
            return self._run_vector(params, art, overlay_text, scale)
        return self._run_raster(params, art, overlay_text, scale)

    # -- all-vector: nest traced artwork + overlay (no embedded raster) --
    def _run_vector(self, params, artwork_text, overlay_text, scale) -> dict:
        W, H = svg_size(artwork_text)
        combined = (
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
            f'{_strip_decl(artwork_text)}{_strip_decl(overlay_text)}</svg>')
        result = {"width": W, "height": H, "out_png": None, "out_svg": None, "vector": True}
        if params.get("out_svg"):
            out_svg = resolve_path(self.config, params["out_svg"])
            out_svg.parent.mkdir(parents=True, exist_ok=True)
            out_svg.write_text(combined, encoding="utf-8")
            result["out_svg"] = str(out_svg)
        if params.get("out_png"):
            out_png = resolve_path(self.config, params["out_png"])
            out_png.parent.mkdir(parents=True, exist_ok=True)
            render_svg_to_png(combined, out_png, W, H, scale=scale, background="#FFFFFF")
            result["out_png"] = str(out_png)
        return result

    # -- raster: embed artwork as <image>, composite the overlay on top --
    def _run_raster(self, params, art_path, overlay_text, scale) -> dict:
        from PIL import Image
        art = Image.open(art_path).convert("RGBA")
        W, H = art.size
        result = {"width": W, "height": H, "out_png": None, "out_svg": None, "vector": False}

        if params.get("out_png"):
            tw, th = int(W * scale), int(H * scale)
            tmp = art_path.with_suffix(".overlay.tmp.png")
            render_svg_to_png(overlay_text, tmp, W, H, scale=scale, background=None)
            ov = Image.open(tmp).convert("RGBA")
            base = art if (tw, th) == (W, H) else art.resize((tw, th), Image.LANCZOS)
            if ov.size != base.size:
                ov = ov.resize(base.size, Image.LANCZOS)
            out = Image.alpha_composite(base, ov)
            out_png = resolve_path(self.config, params["out_png"])
            out_png.parent.mkdir(parents=True, exist_ok=True)
            out.convert("RGB").save(out_png)
            tmp.unlink(missing_ok=True)
            result["out_png"] = str(out_png)

        if params.get("out_svg"):
            mime = _MIME.get(art_path.suffix.lower(), "image/png")
            b64 = base64.b64encode(art_path.read_bytes()).decode()
            layered = (
                f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                f'width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
                f'<image x="0" y="0" width="{W}" height="{H}" preserveAspectRatio="none" '
                f'xlink:href="data:{mime};base64,{b64}"/>{_inner_svg(overlay_text)}</svg>')
            out_svg = resolve_path(self.config, params["out_svg"])
            out_svg.parent.mkdir(parents=True, exist_ok=True)
            out_svg.write_text(layered, encoding="utf-8")
            result["out_svg"] = str(out_svg)
        return result

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"compose_figure_layers failed: {e}", is_error=True)
        bits = []
        if r["out_png"]:
            bits.append(f"composite PNG -> {r['out_png']}")
        if r["out_svg"]:
            bits.append(f"{'all-vector' if r['vector'] else 'layered'} SVG -> {r['out_svg']}")
        msg = f"Composed {r['width']}x{r['height']}: " + "; ".join(bits) + "."
        if r["out_png"]:
            return ToolResult(content=[TextContent(text=msg), png_block(Path(r["out_png"]))], details=r)
        return ToolResult.text(msg, details=r)
