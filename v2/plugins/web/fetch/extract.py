"""HTML -> readable markdown extraction, shared by the fetch providers.

Moved verbatim from web_fetch.py: trafilatura first, readability-lxml +
markdownify fallback, crude tag-strip as last resort. Also holds `sanitize_url`
and the shared size/timeout constants.
"""

from __future__ import annotations

import re

DEFAULT_MAX_CHARS = 20_000
MAX_CHARS_CAP = 100_000  # allow the model to request fuller content
MAX_RESPONSE_BYTES = 750_000
MAX_REDIRECTS = 3
TIMEOUT_SEC = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def sanitize_url(raw: str) -> str:
    """Repair model-injected whitespace, e.g. 'https:// example.com'."""
    url = raw.strip()
    url = re.sub(r"^(https?://)\s+", r"\1", url, flags=re.IGNORECASE)
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        raise ValueError("URL must be http(s)")
    return url


def extract_html(html: str, url: str) -> tuple[str | None, str]:
    """Returns (title, markdown_text). Trafilatura first, readability fallback."""
    try:
        import trafilatura

        text = trafilatura.extract(
            html, url=url, output_format="markdown", include_links=True, include_tables=True
        )
        if text and text.strip():
            meta = trafilatura.extract_metadata(html)
            title = meta.title if meta else None
            return title, text.strip()
    except Exception:
        pass

    try:
        from markdownify import markdownify
        from readability import Document

        doc = Document(html)
        summary_html = doc.summary()
        text = markdownify(summary_html, heading_style="ATX").strip()
        if text:
            return doc.short_title(), text
    except Exception:
        pass

    stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return None, stripped


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) > max_chars:
        return text[:max_chars] + "\n… [content truncated]", True
    return text, False
