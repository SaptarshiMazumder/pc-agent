"""trace_image: bitmap -> vector paths (geometric "dumb" tracing).

The non-semantic vectorizer: it converts shapes/regions to Bezier paths. Good for turning a logo or
a clean line drawing into scalable vectors; it does NOT make text or arrows editable (a label becomes
letter-shaped outlines, not a <text> element — for that use reconstruct_svg).

Tracing needs a backend that isn't bundled (keeps the plugin dependency-free by default). It uses, in
order: the `vtracer` Python package, then a `vtracer` CLI on PATH. If neither is present it returns a
clear, actionable error instead of failing cryptically.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from agentd.application.interfaces.tool import Tool, ToolResult
from agentd.application.run_context import current_workspace


class TraceImageTool(Tool):
    name = "trace_image"
    description = (
        "OPT-IN geometric vectorizer: trace a raster image's color regions -> Bezier `<path>` shapes "
        "in an .svg. Use ONLY when the user EXPLICITLY asks for the artwork itself to become editable "
        "vector shapes (e.g. 'vectorize the whole image', 'make the shapes editable in Illustrator') — "
        "it is LOSSY (many anonymous color-blob paths, large files, slightly degraded look), so it is "
        "NOT a default step. The normal editable deliverable is the layered SVG from compose_layers; "
        "for editable text/arrows use reconstruct_svg. Input: `image`, `out_svg`, optional `mode` "
        "(color|binary) and `precision` (1-8 color detail; default 6 = high fidelity, near-identical "
        "to the raster; lower = fewer paths / cleaner posterized look). Requires a vtracer backend "
        "(Python pkg `vtracer` or the vtracer CLI); returns an actionable error if missing."
    )
    label = "Trace Image"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["image", "out_svg"],
        "properties": {
            "image": {"type": "string", "description": "Path to the raster image to trace."},
            "out_svg": {"type": "string", "description": "Output .svg path."},
            "mode": {"type": "string", "enum": ["color", "binary"], "description": "Color tracing or 2-tone. Default color."},
            "precision": {"type": "integer", "description": "Color detail 1-8. Default 6 (high fidelity, more paths). Lower = fewer paths, cleaner/posterized."},
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

    def _prep_input(self, img: Path, out: Path) -> Path:
        """Re-encode the source into a clean, opaque RGB PNG next to the output before tracing.
        vtracer's Rust image decoder is picky — it fails with "No image file found" on some PNG
        encodings and on cloud-synced (e.g. OneDrive) source paths, even when the file exists. Going
        through Pillow (composite over white, drop alpha) normalizes the bytes and the path so tracing
        is reliable. Returns the temp path (caller cleans it up)."""
        from PIL import Image
        src = Image.open(img).convert("RGBA")
        flat = Image.alpha_composite(Image.new("RGBA", src.size, (255, 255, 255, 255)), src).convert("RGB")
        tmp = out.with_suffix(".trace_src.png")
        flat.save(tmp)
        return tmp

    def _run(self, params: dict) -> dict:
        img = self._resolve(params["image"])
        out = self._resolve(params["out_svg"])
        out.parent.mkdir(parents=True, exist_ok=True)
        mode = params.get("mode", "color")
        precision = max(1, min(8, int(params.get("precision", 6))))   # default = high fidelity
        src = self._prep_input(img, out)                # normalized, reliable input

        try:
            # 1) vtracer python package
            try:
                import vtracer  # type: ignore
            except ImportError:
                vtracer = None
            if vtracer is not None:
                try:
                    vtracer.convert_image_to_svg_py(
                        str(src), str(out),
                        colormode=("color" if mode == "color" else "binary"),
                        color_precision=precision)
                    return {"out_svg": str(out), "backend": "vtracer-py", "precision": precision}
                except BaseException as e:              # pyo3 PanicException is NOT an Exception subclass
                    py_err = e

            # 2) vtracer CLI
            cli = shutil.which("vtracer")
            if cli:
                cmd = [cli, "--input", str(src), "--output", str(out), "--colormode", mode,
                       "--color_precision", str(precision)]
                subprocess.run(cmd, check=True, capture_output=True, text=True)
                return {"out_svg": str(out), "backend": "vtracer-cli", "precision": precision}

            if vtracer is not None:                     # pkg present but the trace itself panicked
                raise RuntimeError(f"vtracer failed to trace the image: {py_err}")
            raise RuntimeError(
                "no tracing backend found. Install one: `pip install vtracer` (Python) or the vtracer "
                "CLI (cargo install vtracer). For EDITABLE text/arrows use reconstruct_svg instead.")
        finally:
            src.unlink(missing_ok=True)                 # clean up the temp input

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"trace_image failed: {e}", is_error=True)
        return ToolResult.text(
            f"Traced -> {r['out_svg']} (backend: {r['backend']}, precision {r.get('precision')}).",
            details=r)
