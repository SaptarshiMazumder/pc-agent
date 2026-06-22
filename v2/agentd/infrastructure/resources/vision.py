"""Rich resource describer (Gemini) — the optional rich_fn for the Resource Manager.

Produces a GOOD one-line description per resource, in the manager's BACKGROUND (off the
agent's path):
  * images           -> a vision CAPTION                         (AGENTD_RESOURCE_VISION=1)
  * scripts/docs/data -> an LLM SUMMARY of the content           (AGENTD_RESOURCE_SUMMARIZE=1)
    (reusing documents.extract_text for docx/pdf/xlsx/pptx)
Two independent opt-in gates because both send content to Google + cost API calls. Returns
"" when a tier is off / unsupported / on error, so the deterministic BasicDescriber stays
the fallback. The real model call runs in a worker thread so it never blocks the loop.
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
    """Return an async ``(kind, path, sample) -> str`` describer, or None when no tier is on
    (or no API key) — in which case the manager stays entirely on BasicDescriber."""
    want_vision = bool(getattr(config, "resource_vision_enabled", False))
    want_summary = bool(getattr(config, "resource_summarize_enabled", False))
    if not (want_vision or want_summary):
        return None
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log.info("resource rich-describe on but no GEMINI_API_KEY -> staying on basic")
        return None

    model = getattr(config, "resource_vision_model", "gemini-2.5-flash")
    timeout_ms = int(getattr(config, "resource_vision_timeout_seconds", 60.0) * 1000)
    client = None  # built lazily on first real use

    def _client():
        nonlocal client
        if client is None:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))
        return client

    def _caption(sample: bytes, mime: str) -> str:
        from google.genai import types
        resp = _client().models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=sample, mime_type=mime), _CAPTION_PROMPT])
        return (getattr(resp, "text", "") or "").strip()

    def _summarize(content: str, name: str) -> str:
        resp = _client().models.generate_content(
            model=model,
            contents=[_SUMMARY_PROMPT.format(name=name, content=content[:_MAX_SUMMARY_CHARS])])
        return (getattr(resp, "text", "") or "").strip()

    async def rich_fn(kind: str, path: Path, sample: bytes) -> str:
        try:
            if kind == KIND_IMAGE:
                if not (want_vision and sample):
                    return ""
                mime = mimetypes.guess_type(str(path))[0] or "image/png"
                text = await asyncio.to_thread(_caption, sample, mime)
            else:
                if not want_summary:
                    return ""
                content = _text_for(path, sample)
                if not content:
                    return ""                                  # opaque binary -> basic
                text = await asyncio.to_thread(_summarize, content, path.name)
        except Exception:  # noqa: BLE001 — any failure -> fall back to the basic description
            log.debug("rich describe failed for %s", path, exc_info=True)
            return ""
        return " ".join(text.split())[:200]                    # collapse to one bounded line

    return rich_fn
