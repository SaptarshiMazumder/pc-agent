"""build_fetch_providers — the fetch chain: httpx first, then browser-render
escalation (only when a browser manager is available)."""

from __future__ import annotations

from agentd.application.interfaces.fetch import FetchProvider
from agentd.infrastructure.tools.fetch.providers import BrowserRenderProvider, HttpxFetchProvider


def build_fetch_providers(config, browser_manager=None) -> list[FetchProvider]:
    providers: list[FetchProvider] = [HttpxFetchProvider()]
    if browser_manager is not None:
        providers.append(BrowserRenderProvider(browser_manager))
    return providers
