"""generate_artwork: text -> a clean RASTER illustration via Gemini's Nano Banana image model.

The illustrated half of the figure pipeline. By default it generates CLEAN, UNLABELLED artwork
(it appends a strong no-text/no-label/no-arrow directive) because in the hybrid pipeline the labels
and arrows are added as an editable vector overlay (render_overlay) — baking text into pixels is
exactly what makes a figure wrong and uneditable. Pass allow_text=true only for a quick one-shot
illustrated figure with no overlay.

Optional `reference_images` steer layout/style (Gemini's restyle/img2img path) — the most reliable
accuracy lever, since the human/source supplies the structure and the model only paints it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentd.application.interfaces.tool import Tool, ToolResult
from agentd.application.run_context import current_workspace
from agentd.application.tool_models import resolve_tool_model, resolve_tool_provider
from agentd.domain.messages import TextContent, ImageContent
import base64

import imagegen_gemini as gem

# The "no-text" half — ALWAYS appended (textless is the whole point; labels are the vector overlay).
# Kept SEPARATE from style so a rich style can never be diluted by a "flat" directive.
_NO_TEXT = (" NO text, NO labels, NO numbers, NO arrows, NO callouts, NO captions, NO legend, "
            "NO measurement marks — render purely the artwork, nothing written.")

# The STYLE half — chosen, not hardcoded. Default is a shaded, volumetric BioRender/Cell-journal
# look (the previous hard-coded 'flat editorial, thin outlines' is exactly what made art look flat).
# The agent's own style words lead; the preset reinforces; _NO_TEXT lands last.
DEFAULT_STYLE = "biorender-3d"
_STYLES = {
    "biorender-3d": (
        " Premium scientific illustration in the BioRender / Cell-journal house style: each structure "
        "richly SHADED and VOLUMETRIC with smooth gradient fills, soft ambient occlusion, gentle rim "
        "light and subtle specular highlights so forms read as three-dimensional; clean confident "
        "outlines, cohesive saturated-pastel biomedical palette, soft cast shadows for depth, crisp "
        "focus, clean white background. Polished, publication-grade, not flat."),
    "cell-journal": (
        " Editorial scientific illustration in the style of a Cell / Nature journal cover: dimensional "
        "gradient-shaded structures, dramatic but clean lighting, rich depth and material detail, "
        "refined palette, white background. Realistic shading, not flat."),
    "watercolor-medical": (
        " Classic medical-atlas illustration (Netter-like): hand-painted gradient shading, layered "
        "tones modelling volume and form, warm anatomical palette, soft edges, subtle texture, "
        "white background."),
    "flat-vector": (
        " Clean flat-vector scientific illustration: thin even outlines, flat or lightly-shaded fills, "
        "soft pastel biomedical palette, minimal depth, white background. (Use only when a flat "
        "schematic look is explicitly wanted.)"),
}

# Prompt for NATIVE vector (recraft) — flat clean shapes trace/export best as real SVG paths.
_VECTOR_STYLE = (
    " Clean flat VECTOR illustration: bold well-defined shapes, limited solid-color palette, crisp "
    "clean outlines, minimal or no gradients, distinct separable parts, white background.")

# How to USE the reference_images for accuracy. Gemini has no ControlNet, so conditioning is a
# strong structure-preservation instruction alongside the reference input.
_CONDITION = {
    "style": " Match the visual STYLE of the reference image(s); the layout may differ.",
    "layout": " Follow the spatial arrangement, structure, and CONNECTIVITY of the reference image(s) "
              "EXACTLY — do not add, remove, move, or recount any structure. Re-render only the style.",
    "sketch": " The reference is a ROUGH SKETCH defining the correct layout. Keep every region in its "
              "place and the same arrangement/connectivity; clean it into polished artwork in the "
              "target style — invent nothing not in the sketch.",
}


class GenerateArtworkTool(Tool):
    name = "generate_artwork"
    plugin = "imagegen"
    needs_model = True
    default_model = gem.DEFAULT_MODEL   # last-resort fallback (config plugins.imagegen.* overrides it)
    description = (
        "Generate a raster scientific illustration from a text prompt. `provider` picks the backend: "
        "'gemini' (default, Gemini 3 Pro Image — best polish, GEMINI_API_KEY), 'fal' or 'replicate' "
        "(cheaper, and with `reference_images` give REAL structural conditioning via ControlNet, so a "
        "sketch/layout is preserved exactly). fal needs FAL_KEY + fal-client ($10 min); replicate "
        "needs REPLICATE_API_TOKEN + `pip install replicate` (no minimum) and accepts a `lora_url` "
        "(e.g. Civitai) on FLUX. `family` = flux (default) or sdxl. "
        "By DEFAULT produces clean UNLABELLED artwork (labels/arrows are added later as an editable "
        "vector overlay) — set allow_text=true only for a standalone illustrated figure. `style` picks "
        "the look (default 'biorender-3d' = richly shaded/volumetric; also 'cell-journal', "
        "'watercolor-medical', 'flat-vector'). Returns the PNG path and the image so you can SEE it."
    )
    label = "Generate Artwork"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["prompt", "out_path"],
        "properties": {
            "prompt": {"type": "string", "description": "What to depict (subject, viewpoint, composition)."},
            "out_path": {"type": "string", "description": "Output .png path (absolute or workspace-relative)."},
            "style": {"type": "string", "enum": ["biorender-3d", "cell-journal", "watercolor-medical", "flat-vector"],
                      "description": "Visual style preset. Default 'biorender-3d' (richly shaded, volumetric). Use 'flat-vector' only for a deliberately flat schematic look."},
            "allow_text": {"type": "boolean", "description": "Allow baked-in text/labels. Default false (recommended for the hybrid overlay flow)."},
            "reference_images": {"type": "array", "items": {"type": "string"},
                                  "description": "Optional paths to reference images for conditioning (sketch/reference)."},
            "conditioning": {"type": "string", "enum": ["style", "layout", "sketch"],
                              "description": "How to use reference_images: 'sketch' (rough layout to clean up — highest accuracy), 'layout' (preserve structure/connectivity exactly), 'style' (match look only). Default 'layout' when reference_images given."},
            "aspect_ratio": {"type": "string", "description": "e.g. '16:9', '1:1', '4:3'. Optional."},
            "provider": {"type": "string", "enum": ["gemini", "fal", "replicate", "flux", "sdxl"],
                          "description": "Image backend host. 'gemini' (default, best polish, GEMINI_API_KEY); 'fal' (fal.ai, $10 min, FAL_KEY); 'replicate' (no minimum, REPLICATE_API_TOKEN). fal/replicate give real ControlNet conditioning. ('flux'/'sdxl' are back-compat aliases for fal + that family.)"},
            "family": {"type": "string", "enum": ["flux", "sdxl", "recraft"],
                        "description": "Model family. 'flux' (default, quality raster); 'sdxl' (mature ControlNet); 'recraft' = NATIVE VECTOR SVG output (real editable shapes, no tracing; replicate only, flatter look)."},
            "tier": {"type": "string", "description": "FLUX tier: 'schnell' (cheapest) | 'dev' (default) | 'pro' (best)."},
            "lora_url": {"type": "string", "description": "Optional LoRA weights URL (e.g. a Civitai/HF download) to apply — Replicate FLUX only."},
            "model": {"type": "string", "description": f"Override the model/endpoint string. For gemini resolves per-call > agent.toml/config plugins.imagegen.tools.generate_artwork > plugins.imagegen default > {gem.DEFAULT_MODEL}; for fal/replicate it overrides the endpoint."},
            "api_key": {"type": "string", "description": "Override key (Gemini: GEMINI_API_KEY/GOOGLE_API_KEY; fal: FAL_KEY; replicate: REPLICATE_API_TOKEN)."},
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
        provider, family = self._route(params)
        # Order matters: the agent's own words lead, then the style reinforces the look, then
        # conditioning, then the no-text directive lands LAST so nothing dilutes "textless".
        prompt = params["prompt"].rstrip(". ") + "."
        if family == "recraft":
            # native vector wants FLAT shapes — the shaded raster presets fight clean SVG output.
            prompt += _VECTOR_STYLE
        else:
            style = params.get("style", DEFAULT_STYLE)
            prompt += _STYLES.get(style, _STYLES[DEFAULT_STYLE])

        refs = [self._resolve(r) for r in params.get("reference_images", [])]
        conditioning = params.get("conditioning", "layout" if refs else None)
        if refs and provider == "gemini":
            # Gemini has no ControlNet, so it needs the conditioning spelled out in the prompt. FLUX/SDXL
            # apply it STRUCTURALLY (ControlNet/img2img) in the backend, so don't dilute their prompt.
            prompt += _CONDITION.get(conditioning, _CONDITION["layout"])
        if not params.get("allow_text"):
            prompt += _NO_TEXT
        out = self._resolve(params["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)

        if provider in ("fal", "replicate"):
            backend = __import__("imagegen_flux" if provider == "fal" else "imagegen_replicate")
            key = backend.resolve_key(params.get("api_key"), self.config)
            # The plugins.imagegen model IS the fal/replicate endpoint (owner/name) here. But the tool's
            # built-in default is a Gemini id, meaningless as an endpoint — so resolve with no gemini
            # fallback and drop any gemini/* (or bare) id, letting the backend pick its family/tier default.
            endpoint = resolve_tool_model(self.config, self.plugin, self.name,
                                          per_call=params.get("model"), default=None)
            if endpoint and (endpoint.startswith("gemini") or "/" not in endpoint):
                endpoint = None
            kw = dict(family=family, tier=params.get("tier"),
                      reference_images=[str(r) for r in refs], conditioning=conditioning,
                      aspect_ratio=params.get("aspect_ratio"), api_key=key,
                      endpoint=endpoint)
            if provider == "replicate" and params.get("lora_url"):
                kw["lora_url"] = params["lora_url"]
            return backend.generate_image(prompt, out, **kw)

        key = gem.resolve_key(params.get("api_key"), self.config)
        model = resolve_tool_model(self.config, self.plugin, self.name,
                                   per_call=params.get("model"), default=self.default_model)
        return gem.generate_image(
            prompt, out,
            model=model,
            api_key=key,
            reference_images=refs,
            aspect_ratio=params.get("aspect_ratio"),
        )

    def _route(self, params) -> tuple[str, str]:
        """Resolve (provider_host, family). The provider (backend SDK) comes from the plugins config
        (plugins.imagegen.provider), a per-agent agent.toml override, or a per-call `provider` — same
        one place as the model. Back-compat: provider 'flux'/'sdxl' == fal + that family."""
        provider = resolve_tool_provider(self.config, self.plugin, self.name,
                                         per_call=params.get("provider"), default="gemini")
        family = params.get("family")
        if provider in ("flux", "sdxl"):
            family = family or provider
            provider = "fal"
        if family == "recraft":            # native-SVG model lives on replicate
            provider = "replicate"
        return provider, (family or "flux")

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"generate_artwork failed: {e}", is_error=True)
        data = base64.b64encode(Path(r["path"]).read_bytes()).decode()
        prov = f" via {r['provider']}/{r.get('mode', '')}".rstrip("/") if r.get("provider") else ""
        svg = f" Editable vector SVG -> {r['svg_path']}." if r.get("svg_path") else ""
        return ToolResult(
            content=[TextContent(text=f"Artwork -> {r['path']} (model {r['model']}{prov}).{svg}"),
                     ImageContent(data=data, mime_type=r["mime"])],
            details=r)
