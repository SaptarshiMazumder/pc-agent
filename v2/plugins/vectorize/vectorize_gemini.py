"""Gemini image-analysis helper for the vectorize plugin (plugin-prefixed; mirrors vision_gemini).
Key from GEMINI_API_KEY / GOOGLE_API_KEY, genai.Client, generate_content with forced JSON."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_MODEL = "gemini-2.5-flash"


def resolve_key(param_key, config) -> str:
    for cand in (
        param_key,
        os.environ.get("GEMINI_API_KEY"),
        os.environ.get("GOOGLE_API_KEY"),
        getattr(config, "gemini_api_key", None),
    ):
        if cand:
            return str(cand)
    raise RuntimeError("no Gemini API key (set GEMINI_API_KEY or GOOGLE_API_KEY)")


def _normalize_model(model: str) -> str:
    """The google-genai SDK wants a BARE id (e.g. 'gemini-2.5-flash'), but the model knob is shared with the
    litellm-based vision tools and often carries a 'gemini/' (or 'google/'/'models/') prefix. Passing that
    straight through makes the SDK request 'models/gemini/…' -> 404. Strip it so one config value works for
    both worlds. (Same fix as figure_art_gemini._normalize_model.)"""
    m = str(model or "").strip()
    changed = True
    while changed:  # strip repeatedly so 'models/gemini/…' fully unwraps
        changed = False
        for pref in ("gemini/", "google/", "models/"):
            if m.startswith(pref):
                m, changed = m[len(pref) :], True
    return m or DEFAULT_MODEL


def analyze_json(image_path: Path, prompt: str, *, model: str, api_key: str):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    resp = client.models.generate_content(
        model=_normalize_model(
            model
        ),  # 'gemini/…' / 'models/…' -> bare id the SDK wants (fixes 404)
        contents=[types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime), prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    t = (resp.text or "").strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        t = t[4:].strip() if t.lower().startswith("json") else t
    return json.loads(t)


def image_dims(image_path: Path):
    from PIL import Image

    with Image.open(image_path) as im:
        return im.size
