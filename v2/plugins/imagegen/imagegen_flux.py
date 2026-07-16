"""fal.ai backend for the imagegen plugin — FLUX (default) and SDXL, with real structural
conditioning via ControlNet / img2img (which is why it beats Gemini for the sketch->figure path).

Three generation modes, chosen from the tool's existing `conditioning` param + reference image:
  • t2i        — plain text-to-image (no reference).
  • img2img    — reference restyled (conditioning="style", or a reference with no mode): keeps rough
                 content, repaints in the target style.
  • controlnet — reference is a STRUCTURE guide (conditioning="sketch"/"layout"): the model paints
                 within your lines/edges, so layout & connectivity are preserved EXACTLY. This is the
                 accuracy lever Gemini can't do.

Dependency-light on purpose: uses the optional `fal-client` package + a FAL_KEY, and errors with an
actionable message if either is missing (so the imagegen plugin still loads without it — same pattern
as trace_image's vtracer backend).

NOTE on endpoint slugs: fal's model catalog ids change over time. They're centralised in _ENDPOINTS
and overridable per call via `endpoint=`, so if fal renames one you fix it in a single place (or pass
the new slug) without touching the tool.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

# tier -> fal endpoint slug, keyed by (family, mode). Verify against the current fal catalog; override
# with the `endpoint` arg if one has been renamed.
_ENDPOINTS = {
    ("flux", "t2i"):        {"schnell": "fal-ai/flux/schnell", "dev": "fal-ai/flux/dev",
                             "pro": "fal-ai/flux-pro/v1.1"},
    ("flux", "img2img"):    {"dev": "fal-ai/flux/dev/image-to-image",
                             "schnell": "fal-ai/flux/schnell"},
    ("flux", "controlnet"): {"canny": "fal-ai/flux-control-lora-canny",
                             "depth": "fal-ai/flux-control-lora-depth"},
    ("sdxl", "t2i"):        {"default": "fal-ai/fast-sdxl"},
    ("sdxl", "img2img"):    {"default": "fal-ai/fast-sdxl/image-to-image"},
    ("sdxl", "controlnet"): {"default": "fal-ai/sdxl-controlnet-union"},
}
DEFAULT_TIER = {"flux": "dev", "sdxl": "default"}

# aspect ratio -> fal image_size preset
_ASPECT = {"1:1": "square_hd", "16:9": "landscape_16_9", "9:16": "portrait_16_9",
           "4:3": "landscape_4_3", "3:4": "portrait_4_3", "square": "square_hd"}

# conditioning -> ControlNet control type (for the controlnet mode)
_CONTROL_TYPE = {"sketch": "canny", "layout": "canny"}   # depth also available via _ENDPOINTS


def resolve_key(param_key, config) -> str:
    for cand in (param_key, os.environ.get("FAL_KEY"), os.environ.get("FALAI_KEY"),
                 getattr(config, "fal_key", None)):
        if cand:
            return str(cand)
    raise RuntimeError(
        "no fal.ai key — set FAL_KEY (get one at fal.ai) to use the flux/sdxl provider.")


def _client(api_key: str):
    os.environ["FAL_KEY"] = api_key                      # fal_client reads FAL_KEY from the env
    try:
        import fal_client
    except ImportError:
        raise RuntimeError(
            "the flux/sdxl provider needs the fal-client package: `pip install fal-client` "
            "(then set FAL_KEY). Or use provider='gemini'.")
    return fal_client


def _mode_for(conditioning, has_ref) -> str:
    if not has_ref:
        return "t2i"
    if conditioning in ("sketch", "layout"):
        return "controlnet"
    return "img2img"                                      # style, or a bare reference


def _endpoint(family, mode, tier, control_type, override):
    if override:
        return override
    table = _ENDPOINTS.get((family, mode)) or _ENDPOINTS[(family, "t2i")]
    if mode == "controlnet" and family == "flux":
        return table.get(control_type or "canny") or next(iter(table.values()))
    return table.get(tier) or table.get(DEFAULT_TIER.get(family, "default")) or next(iter(table.values()))


def generate_image(prompt: str, out_path: Path, *, family: str = "flux", tier: str | None = None,
                   reference_images=None, conditioning: str | None = None,
                   aspect_ratio: str | None = None, strength: float | None = None,
                   steps: int | None = None, api_key: str = "", endpoint: str | None = None) -> dict:
    """Generate one image via fal.ai -> out_path. Returns {path, mime, model, provider, mode}."""
    fal = _client(api_key)
    family = "sdxl" if family == "sdxl" else "flux"
    tier = tier or DEFAULT_TIER[family]
    ref = None
    refs = list(reference_images or [])
    if refs:
        ref = str(refs[0])
    mode = _mode_for(conditioning, bool(ref))
    control_type = _CONTROL_TYPE.get(conditioning or "", "canny")
    ep = _endpoint(family, mode, tier, control_type, endpoint)

    args = {"prompt": prompt, "num_images": 1, "output_format": "png",
            "image_size": _ASPECT.get(aspect_ratio or "1:1", "square_hd")}
    if steps:
        args["num_inference_steps"] = int(steps)

    if ref and mode != "t2i":
        # upload the reference and attach it under the key this mode expects. (fal arg names vary
        # slightly per endpoint; these are the common ones — override via `endpoint` if a model differs.)
        url = fal.upload_file(ref)
        if mode == "img2img":
            args["image_url"] = url
            args["strength"] = float(strength) if strength is not None else 0.72
        else:  # controlnet
            args["control_image_url"] = url
            args["image_url"] = url                       # some controlnet endpoints read image_url

    result = fal.subscribe(ep, arguments=args, with_logs=False)
    images = (result or {}).get("images") or []
    if not images or not images[0].get("url"):
        raise RuntimeError(f"fal returned no image from {ep}: {str(result)[:300]}")
    img_url = images[0]["url"]
    with urllib.request.urlopen(img_url) as resp:
        data = resp.read()
    out_path.write_bytes(data)
    mime = "image/png" if img_url.lower().split("?")[0].endswith(".png") else "image/jpeg"
    return {"path": str(out_path), "mime": mime, "model": ep, "provider": f"fal:{family}", "mode": mode}
