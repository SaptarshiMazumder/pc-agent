"""Thin image-ANALYSIS helper for the vision plugin (plugin-prefixed to avoid sys.path collisions).

`analyze` now routes through the provider-agnostic one-shot (agentd.infrastructure.llm.oneshot →
LiteLLM), so the vision tools' model is a CONFIG knob (the `plugins.vision.*` map, resolved by
resolve_tool_model), not a hardcoded SDK call — point it at Gemini / OpenAI / Anthropic / a local VLM.
The constants below are only the built-in DEFAULTS the resolver falls back to.

This is the agent's "eye" as a TOOL — independent of the reasoning model, so it works even when the
brain is text-only (e.g. DeepSeek) and can't see tool-returned images itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# DEFAULT model for the VLM judge (verify_figure). Bare id => gemini provider (oneshot normalizes).
# Override via config plugins.vision.tools.verify_figure (or the plugins.vision default), or per-agent.
DEFAULT_MODEL = "gemini-2.5-flash"

# DEFAULT grounding model (read_labels_from_image) — the hardest, most accuracy-critical vision task. A Pro
# model locates structures far more precisely than flash, so label leaders land ON the structure.
# Gemini is uniquely strong at spatial grounding; keep this on a Gemini Pro unless you have a reason.
# Override via config plugins.vision.tools.read_labels_from_image (or the plugins.vision default).
GROUNDING_MODEL = "gemini-3.1-pro-preview"


def resolve_key(param_key: str | None, config) -> str:
    for cand in (param_key, os.environ.get("GEMINI_API_KEY"), os.environ.get("GOOGLE_API_KEY"),
                 getattr(config, "gemini_api_key", None)):
        if cand:
            return str(cand)
    raise RuntimeError("no Gemini API key (set GEMINI_API_KEY or GOOGLE_API_KEY)")


def _mime(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def analyze(image_path: Path, prompt: str, *, model: str, api_key: str | None = None,
            want_json: bool) -> str:
    """Return the model's text (or JSON string) for an image + prompt, via the provider-agnostic
    one-shot (LiteLLM). `model` is a litellm id (bare id => gemini); `api_key` is optional (gemini
    falls back to GEMINI_API_KEY/GOOGLE_API_KEY, other providers read their own env key)."""
    from agentd.infrastructure.llm.oneshot import vision_complete

    return vision_complete(model=model, prompt=prompt, image_paths=[image_path],
                           want_json=want_json, api_key=api_key)


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
