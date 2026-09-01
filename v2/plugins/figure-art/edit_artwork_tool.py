"""edit_artwork: targeted image-model edit of existing artwork — change ONE thing, keep the rest.

The "fix the nozzle" tool (FigureLabs' Region Redraw, as a tool). Instead of regenerating a figure
from scratch — which produces a DIFFERENT picture and invalidates every annotation position — this
edits the given image via the image model with a preserve-everything-else contract, optionally
confined to a region. Use it on the layer that is wrong:

  • raster-state figure (not yet vectorized): edit the labelled PNG directly.
  • vectorized figure: edit the TEXTLESS base, then re-run extract_annotations only if the edit
    moved structures that annotations point at; otherwise re-compose over the new base as-is.

Never use it to fix label TEXT on a vectorized figure — that's an element edit (free, drift-free).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

_EDIT_PROMPT = (
    "Reproduce this EXACT image, pixel-for-pixel, changing ONLY the following: {instruction}. "
    "{region}Everything else must remain IDENTICAL: composition, positions, colours, shading, "
    "style, any existing text and lines, and the pure solid white background. Do not add, move, "
    "or restyle anything that was not asked for."
)

_REGION = (
    "Confine ALL changes to the region from {x0}% to {x1}% horizontally and {y0}% to {y1}% "
    "vertically (measured from the top-left corner); outside that region the image must be "
    "unchanged. "
)


class EditArtworkTool(Tool):
    name = "edit_artwork"
    plugin = "figure-art"
    needs_model = True
    model_kind = "image-gen"
    default_model = ""  # inherit the HOUSE image-gen default (config model_defaults)
    description = (
        "Targeted EDIT of an existing image via the image model: change ONLY what `instruction` "
        "asks (optionally confined to `region` = [x, y, w, h] in pixels) and reproduce everything "
        "else pixel-for-pixel — so annotation coordinates stay valid. This is the 'fix the nozzle' "
        "path: NEVER regenerate a figure from scratch for a localized fix (a fresh generation is a "
        "different picture). Edits the raster only: for wrong label TEXT on a vectorized figure, "
        "edit the element in the spec JSON and re-render instead (free, no drift). Returns the "
        "edited PNG (default: <image>_edit.png)."
    )
    label = "Edit Artwork"
    concurrency = "sequential"  # an edit step is usually part of an ordered fix flow
    parameters = {
        "type": "object",
        "required": ["image", "instruction"],
        "properties": {
            "image": {"type": "string", "description": "Path to the image to edit."},
            "instruction": {
                "type": "string",
                "description": "What to change, precisely (e.g. 'make the nozzle cone longer and narrower'). One focused change per call.",
            },
            "region": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 4,
                "maxItems": 4,
                "description": "Optional [x, y, w, h] pixel box to confine the change to (the model is told everything outside must be identical).",
            },
            "out": {
                "type": "string",
                "description": "Output .png path. Default: <image>_edit.png (never overwrites the input).",
            },
            "model": {
                "type": "string",
                "description": "Override the image model. Resolves per-call > config plugins.figure-art > the house image-gen default (config model_defaults).",
            },
            "api_key": {
                "type": "string",
                "description": "Optional BYOK key override (else GEMINI_API_KEY/GOOGLE_API_KEY). Ignored in cloud mode — the platform proxy carries the call.",
            },
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

    def _run(self, params: dict) -> dict:
        img = self._resolve(params["image"])
        if not img.is_file():
            raise FileNotFoundError(f"no such image: {img}")
        out = self._resolve(params.get("out") or str(img.with_name(img.stem + "_edit.png")))
        out.parent.mkdir(parents=True, exist_ok=True)

        region = ""
        if params.get("region"):
            from PIL import Image

            with Image.open(img) as im:
                W, H = im.size
            x, y, w, h = (float(v) for v in params["region"])
            region = _REGION.format(
                x0=round(100 * x / W),
                x1=round(100 * (x + w) / W),
                y0=round(100 * y / H),
                y1=round(100 * (y + h) / H),
            )
        prompt = _EDIT_PROMPT.format(instruction=params["instruction"].strip(), region=region)

        # Through the runtime's model funnel (`self.models`): mode-correct (BYOK direct / cloud
        # proxied+metered / sandbox brokered) with no credential in this tool, ever.
        model = self.resolve_model(self.config, per_call=params.get("model"))
        r = self.models.generate_image(
            model=model,
            prompt=prompt,
            out_path=out,
            api_key=params.get("api_key"),
            reference_images=[img],
        )
        return {"out": r["path"], "model": r["model"], "region": params.get("region")}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"edit_artwork failed: {e}", is_error=True)
        where = f" (confined to region {r['region']})" if r.get("region") else ""
        return ToolResult.text(
            f"Edited -> {r['out']}{where} (model {r['model']}). LOOK at it and verify only the "
            f"asked change happened before composing or delivering.",
            details=r,
            artifacts=[r["out"]],
        )
