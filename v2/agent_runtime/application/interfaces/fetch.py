"""FetchProvider + FetchResult — the contract for fetching a URL's readable content.

The `web_fetch` tool is a dispatcher over a fetch chain: a plain HTTP provider
first, then (when available) a browser-render provider that escalates for
JS-heavy / bot-blocked pages a raw GET can't read. Each backend implements THIS
port; the tool never imports httpx or Playwright.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None
    title: str | None
    text: str
    truncated: bool = False


@runtime_checkable
class FetchProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    async def fetch(self, url: str, max_chars: int) -> FetchResult: ...
