"""Thin Gemini image-ANALYSIS helper for the vision plugin (plugin-prefixed to avoid sys.path
collisions). Mirrors core resources/vision.py: key from GEMINI_API_KEY (fallback GOOGLE_API_KEY),
genai.Client, models.generate_content with an image Part + a prompt. Optional forced-JSON output.

This is the agent's "eye" as a TOOL — independent of the reasoning model, so it works even when the
brain is text-only (e.g. DeepSeek) and can't see tool-returned images itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# A capable multimodal model for the VLM judge (verify_figure).
DEFAULT_MODEL = "gemini-2.5-flash"

# The GROUNDING model — used for spatial localization (extract_anchors), the hardest, most
# accuracy-critical vision task in the pipeline. A Pro model locates structures far more precisely
# than flash, so label leaders land ON the structure instead of in empty space.
GROUNDING_MODEL = "gemini-3.1-pro-preview"


def resolve_key(param_key: str | None, config) -> str:
    for cand in (param_key, os.environ.get("GEMINI_API_KEY"), os.environ.get("GOOGLE_API_KEY"),
                 getattr(config, "gemini_api_key", None)):
        if cand:
            return str(cand)
    raise RuntimeError("no Gemini API key (set GEMINI_API_KEY or GOOGLE_API_KEY)")


def _mime(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def analyze(image_path: Path, prompt: str, *, model: str, api_key: str, want_json: bool) -> str:
    """Return the model's text (or JSON string) for an image + prompt."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    cfg = None
    if want_json:
        cfg = types.GenerateContentConfig(response_mime_type="application/json")
    resp = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=image_path.read_bytes(), mime_type=_mime(image_path)),
                  prompt],
        config=cfg,
    )
    return resp.text or ""


def parse_json(text: str):
    """Tolerant JSON parse — strip ``` fences the model sometimes adds even in JSON mode."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t.strip("`")
        t = t[4:].strip() if t.lower().startswith("json") else t
    return json.loads(t)


def image_dims(image_path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(image_path) as im:
        return im.size
