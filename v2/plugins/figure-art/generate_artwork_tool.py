"""generate_artwork: text -> a clean RASTER illustration via Gemini's Nano Banana image model.

The illustrated half of the figure pipeline. By default it generates CLEAN, UNLABELLED artwork
(it appends a strong no-text/no-label/no-arrow directive) because in the hybrid pipeline the labels
and arrows are added as an editable vector overlay (render_editable_overlay) — baking text into pixels is
exactly what makes a figure wrong and uneditable. Pass allow_text=true only for a quick one-shot
illustrated figure with no overlay.

Optional `reference_images` steer layout/style (Gemini's restyle/img2img path) — the most reliable
accuracy lever, since the human/source supplies the structure and the model only paints it.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace
from agent_runtime.application.tool_models import (
    resolve_tool_model,
    resolve_tool_provider,
    tool_config,
)
from agent_runtime.domain.messages import ImageContent, TextContent

# The "no-text" half — ALWAYS appended (textless is the whole point; labels are the vector overlay).
# Kept SEPARATE from style so a rich style can never be diluted by a "flat" directive.
_NO_TEXT = (
    " NO text, NO labels, NO numbers, NO arrows, NO callouts, NO captions, NO legend, "
    "NO measurement marks — render purely the artwork, nothing written."
)

# The FINISH guard — a cross-cutting INVARIANT, appended to EVERY call (any template, any plain prompt),
# so it holds even when no template is chosen and even when the skill's guidance never runs. It pins the
# two things the user requires absolutely: (1) a scientific ILLUSTRATION, never a photograph or a photo
# of a physical 3D model / diorama; (2) a PURE-WHITE seamless background with NO cast/drop/contact/ground
# shadow. A grey backdrop + a shadow on the floor is exactly what makes art read as a photo of a diorama
# rather than a clean journal figure. Internal shading/gradients/ambient occlusion that model each
# structure's OWN volume stay welcome — only the BACKGROUND and any shadow cast ONTO it are forbidden.
_FINISH = (
    " Rendered as a clean scientific ILLUSTRATION — never a photograph and never a photo of a physical "
    "model or diorama. The subject sits on a PURE solid white (#FFFFFF) seamless background: no backdrop, "
    "no scenery, no floor or table surface, no vignette, and NO cast, drop, contact or ground shadow "
    "beneath or behind the subject. (Shading, gradients and ambient occlusion WITHIN each structure to "
    "model its own volume are welcome; only the BACKGROUND and any shadow cast onto it are forbidden.)"
)

# The STYLE half — chosen, not hardcoded. Default is a shaded, volumetric BioRender/Cell-journal
# look (the previous hard-coded 'flat editorial, thin outlines' is exactly what made art look flat).
# The agent's own style words lead; the preset reinforces; _NO_TEXT lands last.
DEFAULT_STYLE = "biorender-3d"
_STYLES = {
    "biorender-3d": (
        " Premium scientific illustration in the BioRender / Cell-journal house style: each structure "
        "richly SHADED and VOLUMETRIC with smooth gradient fills, soft ambient occlusion, gentle rim "
        "light and subtle specular highlights so forms read as three-dimensional; clean confident "
        "outlines, cohesive saturated-pastel biomedical palette, self-shading (not cast shadows) for "
        "depth, crisp focus, clean white background. Polished, publication-grade, not flat."
    ),
    "cell-journal": (
        " Editorial scientific illustration in the style of a Cell / Nature journal cover: dimensional "
        "gradient-shaded structures, dramatic but clean lighting, rich depth and material detail, "
        "refined palette, white background. Realistic shading, not flat."
    ),
    "watercolor-medical": (
        " Classic medical-atlas illustration (Netter-like): hand-painted gradient shading, layered "
        "tones modelling volume and form, warm anatomical palette, soft edges, subtle texture, "
        "white background."
    ),
    "flat-vector": (
        " Clean flat-vector scientific illustration: thin even outlines, flat or lightly-shaded fills, "
        "soft pastel biomedical palette, minimal depth, white background. (Use only when a flat "
        "schematic look is explicitly wanted.)"
    ),
}

# Prompt for NATIVE vector (recraft) — flat clean shapes trace/export best as real SVG paths.
_VECTOR_STYLE = (
    " Clean flat VECTOR illustration: bold well-defined shapes, limited solid-color palette, crisp "
    "clean outlines, minimal or no gradients, distinct separable parts, white background."
)

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
    plugin = "figure-art"
    needs_model = True
    model_kind = "image-gen"  # OUTPUTS pixels — its picker must offer image-gen models, not text
    default_model = ""  # no tool-specific pick: inherit the HOUSE image-gen default (model_defaults)
    # provider = the image-gen BACKEND SDK (single pick), self-described so a client shows a dropdown.
    # gemini (default) | fal | replicate — see _route(); flux/sdxl are back-compat aliases of fal.
    provider_options = ["gemini", "fal", "replicate"]
    description = (
        "Generate a raster scientific illustration. PREFER an art TEMPLATE: pass `template=<id>` (see "
        "list_templates — e.g. biorender-shaded, ghosted-anatomy, isometric-3d-stem) plus `subject` "
        "(what to depict); the template supplies the curated style, palette, aspect and model, and any "
        "exemplar images are used as style conditioning for a repeatable look. Without a template, pass "
        "a full `prompt` and optionally `style` (biorender-3d|cell-journal|watercolor-medical|"
        "flat-vector). `palette` (hex list) locks colours for cohesion across a figure's assets. "
        "`provider` picks the backend: 'gemini' (default, Gemini 3 Pro Image / Nano Banana Pro — best "
        "text+structure, platform-billed in cloud mode), 'fal'/'replicate' (cheaper, real ControlNet "
        "conditioning; BYOK only — they need your own FAL_KEY/REPLICATE_API_TOKEN). "
        "By DEFAULT produces clean UNLABELLED artwork (labels/arrows are added later as an editable "
        "vector overlay) — set allow_text=true only for a standalone illustrated figure. Returns the "
        "PNG path, the chosen palette, and the image so you can SEE it."
    )
    label = "Generate Artwork"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["out_path"],
        "properties": {
            "template": {
                "type": "string",
                "description": "Art-template id from list_templates (e.g. 'biorender-shaded', 'ghosted-anatomy', 'isometric-3d-stem'). Supplies the curated style/palette/aspect/model + exemplar conditioning. Combine with `subject`.",
            },
            "subject": {
                "type": "string",
                "description": "With a `template`: WHAT to depict (subject, viewpoint, composition) — merged into the template's {subject} slot.",
            },
            "prompt": {
                "type": "string",
                "description": "Full style prompt when NOT using a template. (With a template, `subject` is used; if only `prompt` is given it is treated as the subject.)",
            },
            "out_path": {
                "type": "string",
                "description": "Output .png path (absolute or workspace-relative).",
            },
            "palette": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Hex colours to enforce for cohesion (e.g. across a figure's artwork + flowchart icons). Overrides the template's palette.",
            },
            "style": {
                "type": "string",
                "enum": ["biorender-3d", "cell-journal", "watercolor-medical", "flat-vector"],
                "description": "Fallback visual style preset when NO template is given. Default 'biorender-3d'.",
            },
            "allow_text": {
                "type": "boolean",
                "description": "Allow baked-in text/labels. Default false (recommended for the hybrid overlay flow).",
            },
            "reference_images": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional paths to reference images for conditioning (sketch/reference).",
            },
            "conditioning": {
                "type": "string",
                "enum": ["style", "layout", "sketch"],
                "description": "How to use reference_images: 'sketch' (rough layout to clean up — highest accuracy), 'layout' (preserve structure/connectivity exactly), 'style' (match look only). Default 'layout' when reference_images given.",
            },
            "aspect_ratio": {
                "type": "string",
                "description": "e.g. '16:9', '1:1', '4:3'. Optional.",
            },
            "provider": {
                "type": "string",
                "enum": ["gemini", "fal", "replicate", "flux", "sdxl"],
                "description": "Image backend host. 'gemini' (default, best polish, GEMINI_API_KEY); 'fal' (fal.ai, $10 min, FAL_KEY); 'replicate' (no minimum, REPLICATE_API_TOKEN). fal/replicate give real ControlNet conditioning. ('flux'/'sdxl' are back-compat aliases for fal + that family.)",
            },
            "family": {
                "type": "string",
                "enum": ["flux", "sdxl", "recraft"],
                "description": "Model family. 'flux' (default, quality raster); 'sdxl' (mature ControlNet); 'recraft' = NATIVE VECTOR SVG output (real editable shapes, no tracing; replicate only, flatter look).",
            },
            "tier": {
                "type": "string",
                "description": "FLUX tier: 'schnell' (cheapest) | 'dev' (default) | 'pro' (best).",
            },
            "lora_url": {
                "type": "string",
                "description": "Optional LoRA weights URL (e.g. a Civitai/HF download) to apply — Replicate FLUX only.",
            },
            "model": {
                "type": "string",
                "description": "Override the model/endpoint string. For gemini resolves per-call > agent.toml/config plugins.figure-art.tools.generate_artwork > the house image-gen default (config model_defaults); for fal/replicate it overrides the endpoint.",
            },
            "api_key": {
                "type": "string",
                "description": "Optional BYOK key override (Gemini: else GEMINI_API_KEY/GOOGLE_API_KEY; fal: FAL_KEY; replicate: REPLICATE_API_TOKEN). Ignored in cloud mode — the platform proxy carries the call.",
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
        # House-DEFAULT template (config-driven safety net): if the caller named NO template and wrote no
        # full custom `prompt` (just a `subject`), fall back to the configured default so the DEFAULT look
        # is the shaded BioRender style — even when the routing skill didn't run and the agent forgot to
        # pass a template. Knob: plugins.figure-art.tools.generate_artwork.default_template (agent.toml /
        # config). Empty/unset => no default (unchanged). An explicit template or a full prompt still wins.
        if not params.get("template") and not params.get("prompt") and params.get("subject"):
            dt = tool_config(self.config, self.plugin, self.name, "default_template", default=None)
            if dt:
                params = {**params, "template": dt}
        # A TEMPLATE (agent-authored recipe) supplies the curated prompt, palette, aspect, model and
        # exemplar refs; the plain-prompt path is the fallback. Either way the no-text directive lands
        # LAST so nothing dilutes "textless".
        tmpl = None
        if params.get("template"):
            import figure_art_templates as tpl

            subject = params.get("subject") or params.get("prompt") or ""
            tmpl = tpl.resolve(
                self.config, params["template"], subject, palette_override=params.get("palette")
            )

        provider, family = self._route(params, tmpl)

        if tmpl:
            prompt = tmpl["prompt"].rstrip(". ") + "."
        else:
            base = params.get("prompt") or params.get("subject")
            if not base:
                raise ValueError("provide `prompt` (or `template` + `subject`)")
            prompt = base.rstrip(". ") + "."

        palette = list(params.get("palette") or (tmpl["palette"] if tmpl else []))
        if family == "recraft":
            # native vector wants FLAT shapes — the shaded raster presets fight clean SVG output.
            prompt += _VECTOR_STYLE
        elif tmpl:
            import figure_art_templates as tpl

            prompt += tpl.palette_directive(palette, tmpl["palette_locked"])
            if tmpl["negative"]:
                prompt += f" Do NOT include: {tmpl['negative']}."
        else:
            style = params.get("style", DEFAULT_STYLE)
            prompt += _STYLES.get(style, _STYLES[DEFAULT_STYLE])
            if palette:
                import figure_art_templates as tpl

                prompt += tpl.palette_directive(palette, False)

        # Exemplar images from the template condition the STYLE (repeatable look); user refs follow.
        refs = list(tmpl["exemplars"]) if tmpl else []
        refs += [str(self._resolve(r)) for r in params.get("reference_images", [])]
        refs = [Path(r) for r in refs if Path(r).exists()]
        default_cond = "style" if (tmpl and tmpl["exemplars"]) else ("layout" if refs else None)
        conditioning = params.get("conditioning", default_cond)
        if refs and provider == "gemini":
            # Gemini has no ControlNet, so it needs the conditioning spelled out in the prompt. FLUX/SDXL
            # apply it STRUCTURALLY (ControlNet/img2img) in the backend, so don't dilute their prompt.
            prompt += _CONDITION.get(conditioning, _CONDITION["layout"])
        prompt += _FINISH  # white bg + no cast shadow + illustration-not-photo — a hard invariant
        if not params.get("allow_text"):
            prompt += _NO_TEXT
        aspect = params.get("aspect_ratio") or (tmpl["aspect"] if tmpl else None)
        # resolution precedence: per-call > agent.toml/config knob (a global cap, e.g. "1K" for cheap
        # iteration) > the template's own default. '1K'|'2K'|'4K' for Gemini 3 Pro Image.
        cfg_res = tool_config(self.config, self.plugin, self.name, "resolution", default=None)
        resolution = params.get("resolution") or cfg_res or (tmpl["resolution"] if tmpl else None)
        out = self._resolve(params["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)

        # per-call model wins, else the template's model, else the plugins-config / built-in default.
        model_pc = params.get("model") or (tmpl["model"] if tmpl else None)

        if provider in ("fal", "replicate"):
            backend = __import__("figure_art_flux" if provider == "fal" else "figure_art_replicate")
            key = backend.resolve_key(params.get("api_key"), self.config)
            # The plugins.figure-art model IS the fal/replicate endpoint (owner/name) here. But the tool's
            # built-in default is a Gemini id, meaningless as an endpoint — so resolve with no gemini
            # fallback and drop any gemini/* (or bare) id, letting the backend pick its family/tier default.
            endpoint = resolve_tool_model(
                self.config, self.plugin, self.name, per_call=model_pc, default=None
            )
            if endpoint and (endpoint.startswith("gemini") or "/" not in endpoint):
                endpoint = None
            kw = dict(
                family=family,
                tier=params.get("tier"),
                reference_images=[str(r) for r in refs],
                conditioning=conditioning,
                aspect_ratio=aspect,
                api_key=key,
                endpoint=endpoint,
            )
            if provider == "replicate" and params.get("lora_url"):
                kw["lora_url"] = params["lora_url"]
            r = backend.generate_image(prompt, out, **kw)
            return {
                **r,
                "palette": palette,
                "template": (tmpl["id"] if tmpl else None),
                "aspect": aspect,
            }

        # The default (gemini) backend goes through the runtime's model funnel (`self.models`):
        # BYOK runs direct on the user's key, cloud modes ride the platform proxy and are metered
        # per account, and the sandbox brokers the same call — this tool never sees a credential.
        model = self.resolve_model(self.config, per_call=model_pc)
        r = self.models.generate_image(
            model=model,
            prompt=prompt,
            out_path=out,
            api_key=params.get("api_key"),
            reference_images=refs,
            aspect_ratio=aspect,
            image_size=resolution,
        )
        return {
            **r,
            "palette": palette,
            "template": (tmpl["id"] if tmpl else None),
            "aspect": aspect,
        }

    def _route(self, params, tmpl=None) -> tuple[str, str]:
        """Resolve (provider_host, family). The provider (backend SDK) comes from a per-call `provider`,
        else the chosen template's `provider`, else the plugins config (agent.toml over global). Same one
        place as the model. Back-compat: provider 'flux'/'sdxl' == fal + that family."""
        provider = resolve_tool_provider(
            self.config,
            self.plugin,
            self.name,
            per_call=params.get("provider") or (tmpl["provider"] if tmpl else None),
            default="gemini",
        )
        family = params.get("family")
        if provider in ("flux", "sdxl"):
            family = family or provider
            provider = "fal"
        if family == "recraft":  # native-SVG model lives on replicate
            provider = "replicate"
        return provider, (family or "flux")

    @staticmethod
    def _white_bg_fraction(path: Path, border: int = 8, thr: int = 245) -> float | None:
        """Deterministic check of the `_FINISH` invariant: fraction of the border band that is
        (near-)pure white. The invariant was previously prompt-only — this actually measures it.
        Best-effort: returns None when it can't be computed (never fails the generation)."""
        try:
            from PIL import Image

            im = Image.open(path).convert("RGB")
            W, H = im.size
            b = min(border, W // 2, H // 2)
            px = im.load()
            ok = total = 0
            for y in range(0, H, 2):
                for x in (*range(0, b), *range(W - b, W)):
                    r, g, bl = px[x, y]
                    ok += min(r, g, bl) >= thr
                    total += 1
            for x in range(0, W, 2):
                for y in (*range(0, b), *range(H - b, H)):
                    r, g, bl = px[x, y]
                    ok += min(r, g, bl) >= thr
                    total += 1
            return ok / total if total else None
        except Exception:
            return None

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"generate_artwork failed: {e}", is_error=True)
        data = base64.b64encode(Path(r["path"]).read_bytes()).decode()
        bg = await asyncio.to_thread(self._white_bg_fraction, Path(r["path"]))
        canvas = ""
        if bg is not None:
            r["bg_white_fraction"] = round(bg, 3)
            if bg < 0.95:
                canvas = (
                    f" CANVAS CHECK FAILED: only {bg:.0%} of the border is pure white — the house "
                    f"invariant (pure-white background, no cast shadow, no scenery) looks violated. "
                    f"LOOK at the image; if so, regenerate before building on it."
                )
        prov = f" via {r['provider']}/{r.get('mode', '')}".rstrip("/") if r.get("provider") else ""
        svg = f" Editable vector SVG -> {r['svg_path']}." if r.get("svg_path") else ""
        tmpl = f" template '{r['template']}'." if r.get("template") else ""
        pal = (
            (
                " Palette (reuse for this figure's other assets + the overlay): "
                + ", ".join(r["palette"])
                + "."
            )
            if r.get("palette")
            else ""
        )
        return ToolResult(
            content=[
                TextContent(
                    text=f"Artwork -> {r['path']} (model {r['model']}{prov}).{tmpl}{svg}{pal}{canvas}"
                ),
                ImageContent(data=data, mime_type=r["mime"]),
            ],
            details=r,
        )
