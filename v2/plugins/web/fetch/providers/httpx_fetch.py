"""HttpxFetchProvider — plain HTTP GET + readable-markdown extraction (the
current web_fetch behavior, moved behind the FetchProvider port). Primary
provider; cannot render JS — JS-heavy pages escalate to the browser provider."""

from __future__ import annotations

import re

import httpx

from agentd.application.interfaces.fetch import FetchResult
from fetch.extract import (
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
    TIMEOUT_SEC,
    USER_AGENT,
    extract_html,
    truncate,
)


class HttpxFetchProvider:
    name = "httpx"

    def available(self) -> bool:
        return True

    async def fetch(self, url: str, max_chars: int) -> FetchResult:
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
            import asyncio

            title, text = await asyncio.to_thread(extract_html, text_body, final_url)
        else:
            text = text_body

        text, truncated = truncate(text, max_chars)
        return FetchResult(
            url=url, final_url=final_url, status=status, title=title, text=text, truncated=truncated
        )
