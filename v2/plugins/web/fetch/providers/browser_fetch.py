"""BrowserRenderProvider — render a page with Playwright, then extract its
readable content. Escalation provider for JS-heavy / bot-blocked pages that a
raw HTTP GET (HttpxFetchProvider) can't read (the streaming-site case).

Isolation: opens its OWN page in the shared browser context and closes it, so it
never disturbs the agent's interactive `browser` tool session (active_page/tabs).
Shares cookies/session with the context, which is usually beneficial.
"""

from __future__ import annotations

from agentd.application.interfaces.fetch import FetchResult
from fetch.extract import extract_html, truncate

_NETWORKIDLE_TIMEOUT_MS = 8_000


class BrowserRenderProvider:
    name = "browser_render"

    def __init__(self, manager):
        self._manager = manager  # BrowserManager (or None)

    def available(self) -> bool:
        return self._manager is not None

    async def fetch(self, url: str, max_chars: int) -> FetchResult:
        mgr = self._manager
        await mgr.ensure()
        page = await mgr.context.new_page()  # isolated; never touches active_page
        try:
            resp = await page.goto(url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=_NETWORKIDLE_TIMEOUT_MS)
            except Exception:
                pass
            html = await page.content()
            final_url = page.url
            status = resp.status if resp is not None else None
        finally:
            try:
                await page.close()
            except Exception:
                pass

        title, text = extract_html(html, final_url)
        text, truncated = truncate(text, max_chars)
        return FetchResult(
            url=url, final_url=final_url, status=status, title=title, text=text, truncated=truncated
        )
