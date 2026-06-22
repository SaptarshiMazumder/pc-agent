"""Rich resource describer — the optional rich_fn for the Resource Manager.

Two INDEPENDENT tiers, both in the manager's BACKGROUND (off the agent's path):
  * IMAGES     -> a vision CAPTION via google-genai (Gemini only)   AGENTD_RESOURCE_VISION=1
  * text/docs  -> an LLM SUMMARY via litellm (ANY provider)         AGENTD_RESOURCE_SUMMARIZE=1
So you can keep image captions on Gemini while pointing text summaries at a local/other model
(AGENTD_RESOURCE_SUMMARY_MODEL=lm_studio/qwen | openai/gpt-... | gemini/...). Each tier returns
"" when off / unavailable / on error, so the deterministic BasicDescriber stays the fallback.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from pathlib import Path

from agentd.domain.resource import KIND_IMAGE
from agentd.infrastructure.documents import extract_text, is_document

log = logging.getLogger("agentd")

_CAPTION_PROMPT = (
    "Describe what this image shows in ONE concise line (max ~15 words), so an agent can "
    "decide whether to use it. No preamble, no markdown, just the description."
)
_SUMMARY_PROMPT = (
    "In ONE concise line (max ~20 words), summarize what this file does or contains, so an "
    "agent can decide whether to use it. No preamble, no markdown.\n\nFILE: {name}\n\n{content}"
)
_MAX_SUMMARY_CHARS = 6000


def _text_for(path: Path, sample: bytes) -> str:
    """The text to summarize: the decoded sample if it's text, else extracted document text."""
    if sample and b"\x00" not in sample[:1024]:
        try:
            t = sample.decode("utf-8")
            if t.strip():
                return t
        except UnicodeDecodeError:
            pass
    if is_document(path):
        return extract_text(path) or ""
    return ""


def build_rich_fn(config):
    """Return an async ``(kind, path, sample) -> str`` describer, or None if no tier can run.

    vision tier needs a Gemini key (google-genai); summary tier needs a litellm model.
    """
    want_vision = bool(getattr(config, "resource_vision_enabled", False))
    want_summary = bool(getattr(config, "resource_summarize_enabled", False))

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    vision_model = getattr(config, "resource_vision_model", "gemini-2.5-flash")
    timeout_s = getattr(config, "resource_vision_timeout_seconds", 60.0)
    # summaries use their own litellm model, else the verify model, else the main model
    summary_model = (getattr(config, "resource_summary_model", "")
                     or getattr(config, "verify_model", None) or getattr(config, "model", None))

    vision_ok = want_vision and bool(gemini_key)
    summary_ok = want_summary and bool(summary_model)
    if not (vision_ok or summary_ok):
        return None

    _vclient = None

    def _caption(sample: bytes, mime: str) -> str:           # google-genai (Gemini, vision)
        nonlocal _vclient
        from google import genai
        from google.genai import types
        if _vclient is None:
            _vclient = genai.Client(api_key=gemini_key,
                                    http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
        resp = _vclient.models.generate_content(
            model=vision_model,
            contents=[types.Part.from_bytes(data=sample, mime_type=mime), _CAPTION_PROMPT])
        return (getattr(resp, "text", "") or "").strip()

    async def _summary(content: str, name: str) -> str:      # litellm (ANY provider)
        import litellm
        resp = await litellm.acompletion(
            model=summary_model,
            messages=[{"role": "user", "content":
                       _SUMMARY_PROMPT.format(name=name, content=content[:_MAX_SUMMARY_CHARS])}],
            temperature=0, timeout=timeout_s)
        return (resp.choices[0].message.content or "").strip()

    async def rich_fn(kind: str, path: Path, sample: bytes) -> str:
        try:
            if kind == KIND_IMAGE:
                if not (vision_ok and sample):
                    return ""
                mime = mimetypes.guess_type(str(path))[0] or "image/png"
                text = await asyncio.to_thread(_caption, sample, mime)
            else:
                if not summary_ok:
                    return ""
                content = _text_for(path, sample)
                if not content:
                    return ""
                text = await _summary(content, path.name)
        except Exception:  # noqa: BLE001 — any failure -> fall back to the basic description
            log.debug("rich describe failed for %s", path, exc_info=True)
            return ""
        return " ".join(text.split())[:200]

    return rich_fn
