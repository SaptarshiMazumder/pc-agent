"""Thin Gemini image-generation helper for the figure-art plugin (kept separate + plugin-prefixed so
it can't collide with another plugin's top-level module on sys.path).

Mirrors the SDK pattern the core already uses in resources/vision.py: read the key from
GEMINI_API_KEY (fallback GOOGLE_API_KEY), build a genai.Client, call models.generate_content. The
Nano Banana family returns the image as an inline_data part on generate_content (NOT the separate
images API), so we pull the first image part's bytes out of the response.
"""

from __future__ import annotations

import os
from pathlib import Path

# Gemini 3 Pro Image ("Nano Banana Pro"): the quality tier. We generate TEXTLESS artwork (labels are
# the vector overlay), so the win we want from Pro is SHADING/material/depth fidelity, not text
# rendering — that's exactly where flash-image looked flat. Keeps the same generate_content +
# inline_data path (and reference-image conditioning). Override per call with `model` for a cheaper
# run (e.g. "gemini-2.5-flash-image" / "gemini-3.1-flash-image").
DEFAULT_MODEL = "gemini-3-pro-image"


def resolve_key(param_key: str | None, config) -> str:
    for cand in (param_key, os.environ.get("GEMINI_API_KEY"), os.environ.get("GOOGLE_API_KEY"),
                 getattr(config, "gemini_api_key", None)):
        if cand:
            return str(cand)
    raise RuntimeError("no Gemini API key (set GEMINI_API_KEY or GOOGLE_API_KEY)")


def _normalize_model(model: str) -> str:
    """The google-genai SDK wants a BARE id (e.g. 'gemini-3-pro-image'), but the model knob is shared
    with the litellm-based vision tools and often carries a 'gemini/' (or 'google/'/'models/') prefix.
    Passing that straight through makes the SDK request 'models/gemini/…' -> 404. Strip it here so the
    same config value works for both worlds."""
    m = str(model or "").strip()
    for pref in ("gemini/", "google/", "models/"):
        if m.startswith(pref):
            m = m[len(pref):]
    return m or DEFAULT_MODEL


def _is_not_found(err: Exception) -> bool:
    s = str(err)
    return "404" in s or "not found" in s.lower() or "NOT_FOUND" in s.upper()


def _alt_model(model: str) -> str | None:
    """A model whose preview/GA id drifted: try the -preview variant (or drop it). None if no alt."""
    if model.endswith("-preview"):
        return model[: -len("-preview")]
    return model + "-preview"


def _ref_parts(reference_images):
    from google.genai import types
    parts = []
    for p in reference_images or []:
        path = Path(p)
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        parts.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=mime))
    return parts


def generate_image(prompt: str, out_path: Path, *, model: str, api_key: str,
                   reference_images=None, aspect_ratio: str | None = None,
                   image_size: str | None = None) -> dict:
    """Generate one image -> out_path. `reference_images` (paths) are passed as conditioning input
    (Gemini's restyle/img2img path; it has no ControlNet). `aspect_ratio` (e.g. '4:3') and
    `image_size` ('1K'|'2K'|'4K', Gemini 3 Pro Image only) go through image_config, each applied
    best-effort so an older SDK/model that lacks a field degrades gracefully. Returns {path, mime, model}."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    contents = _ref_parts(reference_images) + [prompt]
    model = _normalize_model(model)                 # 'gemini/…'/'models/…' -> bare id the SDK wants
    cfg = None
    if aspect_ratio or image_size:
        # supported on the image models via image_config; each field applied best-effort so an
        # unsupported one never fails the call (drop it and retry with what remains).
        img_kw = {}
        if aspect_ratio:
            img_kw["aspect_ratio"] = aspect_ratio
        if image_size:
            img_kw["image_size"] = image_size
        for attempt in (img_kw, {"aspect_ratio": aspect_ratio} if aspect_ratio else {}, {}):
            try:
                cfg = (types.GenerateContentConfig(image_config=types.ImageConfig(**attempt))
                       if attempt else None)
                break
            except Exception:
                cfg = None
    # Call, tolerating a preview/GA id drift: a NotFound retries once on the -preview (or GA) variant.
    try:
        resp = client.models.generate_content(model=model, contents=contents, config=cfg)
    except Exception as e:
        alt = _alt_model(model)
        if not (_is_not_found(e) and alt):
            raise
        resp = client.models.generate_content(model=alt, contents=contents, config=cfg)
        model = alt

    for cand in (resp.candidates or []):
        for part in (getattr(cand.content, "parts", None) or []):
            blob = getattr(part, "inline_data", None)
            if blob and getattr(blob, "data", None):
                data = blob.data
                if isinstance(data, str):           # some SDK paths hand back base64 text
                    import base64
                    data = base64.b64decode(data)
                out_path.write_bytes(data)
                return {"path": str(out_path), "mime": blob.mime_type or "image/png", "model": model}
    # no image -> surface any text the model returned instead (often a refusal/explanation)
    txt = getattr(resp, "text", "") or "(no image and no text in response)"
    raise RuntimeError(f"model returned no image: {txt[:300]}")
