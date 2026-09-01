"""CdpBrowserProvider — attach to the user's ALREADY-RUNNING Chrome over the
DevTools protocol (so the agent drives the live profile/cookies, OpenClaw's
profile="user"). Start Chrome with `--remote-debugging-port=9222` and set
plugins.browser.tools.browser.cdp_url = "http://localhost:9222" in config.

Only `_create_context()` differs from the launched provider — all session
behaviour is inherited from BaseBrowserSession."""

from __future__ import annotations

from agent_runtime.application.tool_models import browser_knob
from agent_runtime.infrastructure.tools.browser.providers.base import BaseBrowserSession


class CdpBrowserProvider(BaseBrowserSession):
    mode = "cdp"

    def __init__(self, config):
        import playwright  # noqa: F401  (early ImportError -> factory omits the tool)

        super().__init__(config)
        self.endpoint = browser_knob(config, "cdp_url", None)

    async def _create_context(self):
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        # connect_over_cdp accepts an http://host:port endpoint (Playwright resolves
        # the /json/version websocket itself) or a direct ws:// URL.
        self._browser = await pw.chromium.connect_over_cdp(self.endpoint)
        # An attached Chrome already has its default context with the user's tabs.
        context = (
            self._browser.contexts[0]
            if self._browser.contexts
            else await self._browser.new_context()
        )
        return pw, context

    async def profiles(self) -> dict:
        return {
            "active": "cdp",
            "available": ["cdp"],
            "endpoint": self.endpoint,
            "hint": "Attached to your running Chrome via CDP; it uses your live profile/cookies.",
        }
