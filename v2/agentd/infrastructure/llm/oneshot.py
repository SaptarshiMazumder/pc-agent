"""One-shot multimodal completion — a single image+prompt call to ANY provider via LiteLLM.

The reusable backend for model-bearing VISION tools (extract_anchors grounding, verify_figure judge,
image captioning). It's the provider-agnostic replacement for calling google-genai directly: pass a
litellm "provider/model" id and it routes to Gemini / OpenAI / Anthropic / a local model alike, so a
tool's model becomes a config knob (see application/tool_models.py) instead of a hardcoded SDK call.

Deliberately NARROW: one prompt, N images, optional forced-JSON, returns the model's text. It does NOT
stream and has no tools — the agent loop's `litellm_stream` stays the place for conversational calls.

Coordinate-accuracy note: Gemini is uniquely good at spatial grounding (the extract_anchors prompt is
written in its native 0-1000 convention). Routing a "gemini/..." model here hits Gemini, so accuracy is
unchanged; pointing grounding at a non-Gemini model is possible but the localization quality is on the
caller — the plumbing is provider-agnostic, the training is not.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path


def normalize_model(model: str) -> str:
    """Make a model id litellm-routable. A bare id (no "provider/") implies gemini — this preserves
    the plugins' historical bare Gemini constants ("gemini-2.5-flash") while letting config pass a
    fully-qualified "openai/gpt-4.1-mini" straight through."""
    m = (model or "").strip()
    return m if "/" in m else f"gemini/{m}"


def _image_part(path: Path) -> dict:
    data = base64.b64encode(path.read_bytes()).decode()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def vision_complete(*, model: str, prompt: str, image_paths, want_json: bool = False,
                    api_key: str | None = None, timeout: float | None = None) -> str:
    """One image+prompt call -> the model's text (or JSON string in JSON mode).

    `model` is a litellm id (bare id => gemini). `image_paths` is one path or a list. `api_key`
    overrides the provider's env key; for a gemini model with no explicit key we fall back to
    GEMINI_API_KEY/GOOGLE_API_KEY so the historical Gemini-key setup keeps working. Synchronous —
    call it from a worker thread (the vision tools already run `_run` via asyncio.to_thread)."""
    import litellm

    litellm.suppress_debug_info = True
    model = normalize_model(model)
    if not api_key and model.startswith("gemini/"):
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    paths = [image_paths] if isinstance(image_paths, (str, Path)) else list(image_paths)
    content = [{"type": "text", "text": prompt}]
    content.extend(_image_part(Path(p)) for p in paths)

    kwargs: dict = {"model": model, "messages": [{"role": "user", "content": content}]}
    if api_key:
        kwargs["api_key"] = api_key
    if want_json:
        # OpenAI-style JSON mode; litellm maps it to each provider's own (Gemini
        # response_mime_type, Anthropic, ...). Providers that lack it degrade to plain text,
        # which the caller's tolerant JSON parse still handles.
        kwargs["response_format"] = {"type": "json_object"}
    if timeout:
        kwargs["request_timeout"] = timeout

    resp = litellm.completion(**kwargs)
    return (resp.choices[0].message.content or "") if resp.choices else ""
