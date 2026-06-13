"""web_fetch tool: fetch a URL and extract readable content as markdown.

Pipeline (mirrors the reference): sanitize URL -> httpx GET with browser UA,
3 redirects, 750KB body cap -> if HTML: trafilatura markdown extraction
(readability-lxml + markdownify fallback) -> truncate -> TTL cache."""

from __future__ import annotations

import asyncio
import re
import time

import httpx

from . import Tool, ToolResult

DEFAULT_MAX_CHARS = 20_000
MAX_RESPONSE_BYTES = 750_000
MAX_REDIRECTS = 3
TIMEOUT_SEC = 30
CACHE_TTL_SEC = 900
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_CACHE: dict[str, tuple[float, dict]] = {}


def sanitize_url(raw: str) -> str:
    """Repair model-injected whitespace, e.g. 'https:// example.com'."""
    url = raw.strip()
    url = re.sub(r"^(https?://)\s+", r"\1", url, flags=re.IGNORECASE)
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        raise ValueError("URL must be http(s)")
    return url


def _extract_html(html: str, url: str) -> tuple[str | None, str]:
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

    # last resort: strip tags crudely
    stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return None, stripped


async def fetch_url(url: str, max_chars: int) -> dict:
    cache_key = f"{url}|{max_chars}"
    hit = _CACHE.get(cache_key)
    if hit and hit[0] > time.time():
        return {**hit[1], "cached": True}

    async with httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        timeout=TIMEOUT_SEC,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    ) as client:
        async with client.stream("GET", url) as resp:
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) >= MAX_RESPONSE_BYTES:
                    break
            status = resp.status_code
            final_url = str(resp.url)
            content_type = resp.headers.get("content-type", "")

    text_body = body.decode("utf-8", errors="replace")
    title = None
    if "html" in content_type or re.search(r"<\s*html", text_body[:1000], re.IGNORECASE):
        title, text = await asyncio.to_thread(_extract_html, text_body, final_url)
    else:
        text = text_body

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + "\n… [content truncated]"

    result = {
        "url": url,
        "finalUrl": final_url,
        "status": status,
        "title": title,
        "text": text,
        "truncated": truncated,
    }
    _CACHE[cache_key] = (time.time() + CACHE_TTL_SEC, result)
    return result


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "Fetch a URL and return its readable content as markdown."
    label = "Web Fetch"
    parameters = {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "description": "HTTP(S) URL to fetch."},
            "max_chars": {
                "type": "integer",
                "minimum": 100,
                "description": f"Max characters returned (default {DEFAULT_MAX_CHARS}).",
            },
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            url = sanitize_url(params["url"])
        except ValueError as e:
            return ToolResult.text(str(e), is_error=True)
        max_chars = params.get("max_chars", DEFAULT_MAX_CHARS)
        try:
            result = await fetch_url(url, max_chars)
        except httpx.HTTPError as e:
            return ToolResult.text(f"web_fetch failed: {type(e).__name__}: {e}", is_error=True)

        header = f"{result['finalUrl']} (status {result['status']})"
        if result.get("title"):
            header += f"\n# {result['title']}"
        return ToolResult.text(f"{header}\n\n{result['text']}", details=result)
