"""Replicate backend for the imagegen plugin — FLUX (default) and SDXL, with ControlNet / img2img
structural conditioning and optional Civitai/HF LoRAs.

Same three-mode design as the fal backend (t2i / img2img / controlnet), chosen from the tool's
`conditioning` param. Why also offer Replicate: it's **pure pay-as-you-go with no minimum top-up**
(fal needs a $10 minimum), and it hosts the same FLUX/SDXL family plus community models — and it
takes a **LoRA URL** (e.g. a Civitai download link) so you can apply a domain/style fine-tune.

Dependency-light: uses the optional `replicate` package + a REPLICATE_API_TOKEN, and errors with an
actionable message if either is missing (so the plugin still loads without it).

NOTE on model slugs: Replicate's model ids/versions drift. They're centralised in _ENDPOINTS and
overridable per call via `endpoint=` (an `owner/name` or `owner/name:version` string), so a rename is
a one-line fix. Input-field names (image / control_image / prompt_strength / lora_weights) also vary
per model — the common ones are used here; override the model via `endpoint` if one differs.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

# (family, mode) -> {tier/control_type: "owner/name"} on Replicate.
_ENDPOINTS = {
    ("flux", "t2i"):        {"schnell": "black-forest-labs/flux-schnell",
                             "dev": "black-forest-labs/flux-dev",
                             "pro": "black-forest-labs/flux-1.1-pro"},
    ("flux", "img2img"):    {"dev": "black-forest-labs/flux-dev",
                             "schnell": "black-forest-labs/flux-schnell"},
    ("flux", "controlnet"): {"canny": "black-forest-labs/flux-canny-dev",
                             "depth": "black-forest-labs/flux-depth-dev"},
    ("sdxl", "t2i"):        {"default": "stability-ai/sdxl"},
    ("sdxl", "img2img"):    {"default": "stability-ai/sdxl"},
    ("sdxl", "controlnet"): {"default": "lucataco/sdxl-controlnet"},
    # Recraft V3 SVG — NATIVE vector output (real shapes, no tracing). t2i only.
    ("recraft", "t2i"):     {"default": "recraft-ai/recraft-v3-svg"},
}
DEFAULT_TIER = {"flux": "dev", "sdxl": "default", "recraft": "default"}
_CONTROL_TYPE = {"sketch": "canny", "layout": "canny"}

# aspect ratio -> SDXL width/height (FLUX on Replicate takes an aspect_ratio string directly)
_SDXL_WH = {"1:1": (1024, 1024), "16:9": (1344, 768), "9:16": (768, 1344),
            "4:3": (1152, 896), "3:4": (896, 1152), "square": (1024, 1024)}

# aspect ratio -> Recraft `size` enum string
_RECRAFT_SIZE = {"1:1": "1024x1024", "16:9": "1365x1024", "9:16": "1024x1365",
                 "4:3": "1365x1024", "3:4": "1024x1365", "square": "1024x1024"}


def resolve_key(param_key, config) -> str:
    for cand in (param_key, os.environ.get("REPLICATE_API_TOKEN"), os.environ.get("REPLICATE_API_KEY"),
                 getattr(config, "replicate_api_token", None)):
        if cand:
            return str(cand)
    raise RuntimeError(
        "no Replicate token — set REPLICATE_API_TOKEN (get one at replicate.com, no minimum top-up) "
        "to use provider='replicate'.")


def _client(api_key: str):
    os.environ["REPLICATE_API_TOKEN"] = api_key           # replicate reads this from the env
    try:
        import replicate
    except ImportError:
        raise RuntimeError(
            "the replicate provider needs the replicate package: `pip install replicate` "
            "(then set REPLICATE_API_TOKEN). Or use provider='gemini'.")
    return replicate


def _mode_for(conditioning, has_ref) -> str:
    if not has_ref:
        return "t2i"
    if conditioning in ("sketch", "layout"):
        return "controlnet"
    return "img2img"


def _endpoint(family, mode, tier, control_type, override):
    if override:
        return override
    table = _ENDPOINTS.get((family, mode)) or _ENDPOINTS[(family, "t2i")]
    if mode == "controlnet":
        key = control_type if family == "flux" else "default"
        return table.get(key) or next(iter(table.values()))
    return table.get(tier) or table.get(DEFAULT_TIER.get(family, "default")) or next(iter(table.values()))


def _build_input(family, mode, prompt, aspect_ratio, ref_file, strength, steps, lora_url, lora_scale):
    inp = {"prompt": prompt}
    if family == "flux":
        inp["aspect_ratio"] = aspect_ratio or "1:1"
        inp["output_format"] = "png"
        if mode == "img2img":
            inp["image"] = ref_file
            inp["prompt_strength"] = float(strength) if strength is not None else 0.72
        elif mode == "controlnet":
            inp["control_image"] = ref_file
        if lora_url:
            inp["lora_weights"] = lora_url
            inp["lora_scale"] = float(lora_scale) if lora_scale is not None else 1.0
    else:  # sdxl
        w, h = _SDXL_WH.get(aspect_ratio or "1:1", (1024, 1024))
        inp["width"], inp["height"] = w, h
        if mode == "img2img":
            inp["image"] = ref_file
            inp["prompt_strength"] = float(strength) if strength is not None else 0.72
        elif mode == "controlnet":
            inp["image"] = ref_file                       # the control/condition image
            inp["condition_scale"] = 0.8
    if steps:
        inp["num_inference_steps"] = int(steps)
    return inp


def _save_output(out, out_path: Path) -> str:
    """Write Replicate's output (list/FileOutput/URL string) to out_path. Returns the mime type."""
    item = out[0] if isinstance(out, (list, tuple)) and out else out
    if hasattr(item, "read"):                             # newer client: FileOutput
        data = item.read()
        out_path.write_bytes(data if isinstance(data, (bytes, bytearray)) else str(data).encode())
        url = str(getattr(item, "url", "") or "")
    else:
        url = getattr(item, "url", None) or (item if isinstance(item, str) else str(item))
        with urllib.request.urlopen(url) as resp:
            out_path.write_bytes(resp.read())
    return "image/png" if url.lower().split("?")[0].endswith(".png") or not url else "image/jpeg"


def _render_preview(svg_text: str, png_path: Path):
    """Rasterize a preview PNG from SVG text (so the agent can SEE a native-vector result, since a
    vision model can't view raw SVG). Reuses the figures plugin's browser renderer."""
    import sys
    figures_dir = str(Path(__file__).resolve().parent.parent / "figures")
    if figures_dir not in sys.path:
        sys.path.insert(0, figures_dir)
    from figures_common import render_svg_to_png, svg_size
    W, H = svg_size(svg_text)
    render_svg_to_png(svg_text, png_path, W, H, scale=1, background="#FFFFFF")
    return W, H


def _generate_recraft(replicate, prompt, out_path: Path, aspect_ratio, style, endpoint) -> dict:
    """Recraft V3 SVG — the artwork is born as REAL vector shapes (no tracing). Saves the .svg (the
    editable deliverable) and a .png preview (for the agent's eye)."""
    model = endpoint or _ENDPOINTS[("recraft", "t2i")]["default"]
    inp = {"prompt": prompt, "size": _RECRAFT_SIZE.get(aspect_ratio or "1:1", "1024x1024")}
    if style:
        inp["style"] = style
    out = replicate.run(model, input=inp)
    item = out[0] if isinstance(out, (list, tuple)) and out else out
    if hasattr(item, "read"):                             # FileOutput -> svg bytes
        data = item.read()
        svg = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
    else:                                                 # URL string
        url = getattr(item, "url", None) or (item if isinstance(item, str) else str(item))
        with urllib.request.urlopen(url) as resp:
            svg = resp.read().decode("utf-8")
    svg_path = out_path.with_suffix(".svg")
    svg_path.write_text(svg, encoding="utf-8")
    png_path = out_path if out_path.suffix.lower() == ".png" else out_path.with_suffix(".png")
    _render_preview(svg, png_path)
    return {"path": str(png_path), "mime": "image/png", "svg_path": str(svg_path),
            "model": model, "provider": "replicate:recraft", "mode": "vector", "vector": True}


def generate_image(prompt: str, out_path: Path, *, family: str = "flux", tier: str | None = None,
                   reference_images=None, conditioning: str | None = None,
                   aspect_ratio: str | None = None, strength: float | None = None,
                   steps: int | None = None, lora_url: str | None = None,
                   lora_scale: float | None = None, style: str | None = None, api_key: str = "",
                   endpoint: str | None = None) -> dict:
    """Generate one image via Replicate -> out_path. Returns {path, mime, model, provider, mode}."""
    replicate = _client(api_key)
    if family == "recraft":                               # native SVG, its own t2i-only path
        return _generate_recraft(replicate, prompt, out_path, aspect_ratio, style, endpoint)
    family = "sdxl" if family == "sdxl" else "flux"
    tier = tier or DEFAULT_TIER[family]
    refs = list(reference_images or [])
    ref = str(refs[0]) if refs else None
    mode = _mode_for(conditioning, bool(ref))
    control_type = _CONTROL_TYPE.get(conditioning or "", "canny")
    model = _endpoint(family, mode, tier, control_type, endpoint)

    handles = []
    try:
        ref_file = None
        if ref and mode != "t2i":
            ref_file = open(ref, "rb")                    # replicate uploads file inputs
            handles.append(ref_file)
        inp = _build_input(family, mode, prompt, aspect_ratio, ref_file, strength, steps,
                           lora_url, lora_scale)
        out = replicate.run(model, input=inp)
    finally:
        for f in handles:
            f.close()

    if not out:
        raise RuntimeError(f"replicate returned no output from {model}")
    mime = _save_output(out, out_path)
    return {"path": str(out_path), "mime": mime, "model": model,
            "provider": f"replicate:{family}", "mode": mode}
