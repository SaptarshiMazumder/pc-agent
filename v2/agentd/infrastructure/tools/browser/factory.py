"""build_browser_provider — select the browser backend (Playwright today;
remote-CDP / cloud later). Returns None if Playwright isn't installed, so the
browser tool (and the fetch browser-render escalation) are simply omitted."""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")


def build_browser_provider(config):
    try:
        from agentd.infrastructure.tools.browser.providers.playwright import (
            PlaywrightBrowserProvider,
        )

        return PlaywrightBrowserProvider(config)
    except ImportError:
        log.warning("playwright not installed; browser tool disabled")
        return None
